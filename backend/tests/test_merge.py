"""Merging an extraction into a stored profile.

No database: the merge operates on ORM objects and a session, and the rules
being tested are about which value wins, not about SQL. A recording stub stands
in for the session so the objects stay in memory and the tests stay fast.

What is actually under test is one policy, applied four times: an inference may
never overwrite something the student stated. Every kind of fact -- skill, tag,
free-text entry, experience level -- gets that check, because a rule enforced in
three places out of four is not a rule.
"""

from __future__ import annotations

import unittest

from app.conversation.merge import merge_profile_extraction
from app.database import models
from app.knowledge.loader import load_knowledge_base
from app.llm.schemas import TurnAnalysis
from tests.fakes import analysis

KB = load_knowledge_base()


class RecordingSession:
    """Enough of a Session for the merge: it adds and deletes, nothing else."""

    def __init__(self):
        self.added: list[object] = []
        self.deleted: list[object] = []

    def add(self, instance) -> None:
        self.added.append(instance)

    def delete(self, instance) -> None:
        self.deleted.append(instance)


class MergeTestCase(unittest.TestCase):
    def setUp(self):
        self.session = RecordingSession()
        self.profile = models.StudentProfile()

    # -- helpers ----------------------------------------------------------

    def merge(self, payload: dict):
        updates = TurnAnalysis.model_validate(payload).profile_updates
        return merge_profile_extraction(self.session, self.profile, updates)

    def give_skill(self, skill_id: str, proficiency: float, source: str):
        self.profile.skills.append(
            models.StudentProfileSkill(
                skill_id=skill_id, proficiency=proficiency, source=source
            )
        )

    def give_tag(self, tag_type: str, tag: str, source: str):
        self.profile.tags.append(
            models.StudentProfileTag(tag_type=tag_type, tag=tag, source=source)
        )

    def skill(self, skill_id: str):
        return next(s for s in self.profile.skills if s.skill_id == skill_id)


class TestAddingFacts(MergeTestCase):
    def test_a_new_skill_is_added_with_its_source(self):
        report = self.merge(analysis(skills=[("python", 0.8, "explicit")]))
        self.assertEqual(self.skill("python").proficiency, 0.8)
        self.assertEqual(self.skill("python").source, "explicit")
        self.assertIn("skill:python", report.added)
        self.assertTrue(report.changed)

    def test_tags_land_in_the_right_type(self):
        self.merge(
            analysis(
                interests=[("ai_ml", "explicit")],
                work_preferences=[("building", "inferred")],
            )
        )
        by_type = {(t.tag_type, t.tag): t.source for t in self.profile.tags}
        self.assertEqual(by_type[("interest", "ai_ml")], "explicit")
        self.assertEqual(by_type[("work_preference", "building")], "inferred")

    def test_free_text_entries_land_in_the_right_type(self):
        self.merge(
            analysis(
                strengths=[("explains things well", "explicit")],
                dislikes=[("heavy statistics", "explicit")],
                goals=[("work on AI products", "explicit")],
            )
        )
        by_type = {e.entry_type: e.content for e in self.profile.entries}
        self.assertEqual(by_type["strength"], "explains things well")
        self.assertEqual(by_type["dislike"], "heavy statistics")
        self.assertEqual(by_type["goal"], "work on AI products")

    def test_experience_is_recorded_with_its_source(self):
        self.merge(analysis(experience=("intermediate", "inferred")))
        self.assertEqual(self.profile.experience_level, "intermediate")
        self.assertEqual(self.profile.experience_level_source, "inferred")

    def test_an_empty_extraction_changes_nothing(self):
        report = self.merge(analysis())
        self.assertFalse(report.changed)
        self.assertEqual(self.profile.skills, [])
        self.assertEqual(self.session.added, [])


class TestExplicitBeatsInferred(MergeTestCase):
    """The rule that makes the source column worth having."""

    def test_an_inference_cannot_overwrite_a_stated_skill(self):
        self.give_skill("python", 0.8, "explicit")
        report = self.merge(analysis(skills=[("python", 0.3, "inferred")]))
        self.assertEqual(self.skill("python").proficiency, 0.8)
        self.assertEqual(self.skill("python").source, "explicit")
        self.assertIn("skill:python", report.protected)
        self.assertFalse(report.changed)

    def test_a_statement_overwrites_an_inference(self):
        self.give_skill("python", 0.3, "inferred")
        report = self.merge(analysis(skills=[("python", 0.8, "explicit")]))
        self.assertEqual(self.skill("python").proficiency, 0.8)
        self.assertEqual(self.skill("python").source, "explicit")
        self.assertIn("skill:python", report.updated)

    def test_a_newer_statement_replaces_an_older_one(self):
        self.give_skill("python", 0.4, "explicit")
        self.merge(analysis(skills=[("python", 0.9, "explicit")]))
        self.assertEqual(self.skill("python").proficiency, 0.9)

    def test_a_newer_inference_replaces_an_older_one(self):
        self.give_skill("sql", 0.3, "inferred")
        self.merge(analysis(skills=[("sql", 0.6, "inferred")]))
        self.assertEqual(self.skill("sql").proficiency, 0.6)

    def test_an_inference_cannot_weaken_a_stated_tag(self):
        self.give_tag("interest", "ai_ml", "explicit")
        report = self.merge(analysis(interests=[("ai_ml", "inferred")]))
        self.assertEqual(self.profile.tags[0].source, "explicit")
        self.assertIn("interest:ai_ml", report.protected)

    def test_a_statement_promotes_an_inferred_tag(self):
        self.give_tag("interest", "ai_ml", "inferred")
        report = self.merge(analysis(interests=[("ai_ml", "explicit")]))
        self.assertEqual(self.profile.tags[0].source, "explicit")
        self.assertIn("interest:ai_ml", report.updated)

    def test_an_inference_cannot_overwrite_a_stated_experience_level(self):
        self.profile.experience_level = "advanced"
        self.profile.experience_level_source = "explicit"
        report = self.merge(analysis(experience=("beginner", "inferred")))
        self.assertEqual(self.profile.experience_level, "advanced")
        self.assertIn("experience:advanced", report.protected)

    def test_a_level_stored_without_a_source_is_treated_as_stated(self):
        """Rows written before the column existed must not be silently downgraded."""
        self.profile.experience_level = "advanced"
        self.profile.experience_level_source = None
        self.merge(analysis(experience=("beginner", "inferred")))
        self.assertEqual(self.profile.experience_level, "advanced")


class TestNothingDisappears(MergeTestCase):
    def test_omission_does_not_delete(self):
        self.give_skill("python", 0.8, "explicit")
        self.give_tag("interest", "data", "explicit")
        self.merge(analysis(skills=[("sql", 0.5, "explicit")]))
        self.assertEqual({s.skill_id for s in self.profile.skills}, {"python", "sql"})
        self.assertEqual(len(self.profile.tags), 1)

    def test_a_repeated_identical_fact_is_not_reported_as_a_change(self):
        self.give_skill("python", 0.8, "explicit")
        report = self.merge(analysis(skills=[("python", 0.8, "explicit")]))
        self.assertFalse(report.changed)

    def test_an_identical_entry_is_not_duplicated(self):
        self.merge(analysis(goals=[("work on AI products", "explicit")]))
        self.merge(analysis(goals=[("Work on AI Products", "explicit")]))
        self.assertEqual(len(self.profile.entries), 1)


class TestCorrections(MergeTestCase):
    """A student taking something back is the one thing that may delete."""

    def test_a_withdrawn_interest_is_removed(self):
        self.give_tag("interest", "data", "explicit")
        report = self.merge(analysis(removed_interests=["data"]))
        self.assertEqual(self.profile.tags, [])
        self.assertIn("interest:data", report.removed)
        self.assertTrue(report.changed)

    def test_a_withdrawn_skill_is_removed(self):
        self.give_skill("statistics", 0.6, "explicit")
        self.give_skill("python", 0.8, "explicit")
        self.merge(analysis(removed_skills=["statistics"]))
        self.assertEqual({s.skill_id for s in self.profile.skills}, {"python"})

    def test_withdrawing_something_we_never_had_is_harmless(self):
        report = self.merge(analysis(removed_interests=["data"]))
        self.assertEqual(report.removed, [])

    def test_a_withdrawal_in_the_same_turn_wins_over_the_statement(self):
        """'I like data -- actually no' ends with the withdrawal."""
        report = self.merge(
            analysis(interests=[("data", "explicit")], removed_interests=["data"])
        )
        self.assertEqual(self.profile.tags, [])
        self.assertIn("interest:data", report.removed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
