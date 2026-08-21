"""Structured LLM output: validation, vocabulary filtering, and the schema.

No database and no provider. These tests are about the boundary between "the
model said something" and "the application believes something", which is exactly
where a hallucination has to be stopped.

The distinction being tested throughout: a malformed payload is rejected whole,
while an out-of-vocabulary *value* is dropped and the rest of the payload is
kept. Both are safe; only one of them throws away good information.
"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.knowledge.loader import load_knowledge_base
from app.llm.schemas import (
    INTENTS,
    ProfileExtraction,
    TurnAnalysis,
    filter_to_vocabulary,
    parse_turn_analysis,
    turn_analysis_schema,
)
from tests.fakes import analysis, empty_analysis

KB = load_knowledge_base()


class TestShapeValidation(unittest.TestCase):
    """Pydantic layer: the payload is either usable or it is not."""

    def test_a_well_formed_payload_validates(self):
        result = parse_turn_analysis(
            analysis(
                "profile_update",
                skills=[("python", 0.8, "explicit")],
                interests=[("ai_ml", "explicit")],
            ),
            KB,
        )
        self.assertEqual(result.analysis.intent, "profile_update")
        self.assertEqual(result.analysis.profile_updates.skills[0].skill_id, "python")
        self.assertEqual(result.analysis.profile_updates.skills[0].proficiency, 0.8)
        self.assertFalse(result.had_rejections)

    def test_every_documented_intent_is_accepted(self):
        for intent in INTENTS:
            with self.subTest(intent=intent):
                result = parse_turn_analysis(empty_analysis(intent), KB)
                self.assertEqual(result.analysis.intent, intent)

    def test_an_unknown_intent_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_turn_analysis(empty_analysis("become_an_astronaut"), KB)

    def test_proficiency_outside_the_unit_interval_is_rejected(self):
        for proficiency in (-0.1, 1.5, 89):
            with self.subTest(proficiency=proficiency):
                with self.assertRaises(ValidationError):
                    parse_turn_analysis(
                        analysis(skills=[("python", proficiency, "explicit")]), KB
                    )

    def test_an_unknown_source_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_turn_analysis(analysis(skills=[("python", 0.8, "probably")]), KB)

    def test_an_unknown_experience_level_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_turn_analysis(analysis(experience=("expert", "explicit")), KB)

    def test_a_blank_entry_is_rejected(self):
        with self.assertRaises(ValidationError):
            parse_turn_analysis(analysis(strengths=[("   ", "explicit")]), KB)

    def test_entry_content_is_trimmed(self):
        result = parse_turn_analysis(
            analysis(goals=[("  work on AI products  ", "explicit")]), KB
        )
        self.assertEqual(
            result.analysis.profile_updates.goals[0].content, "work on AI products"
        )

    def test_extra_fields_are_rejected(self):
        """An invented field is a signal the model is answering a different question."""
        payload = empty_analysis()
        payload["career_score"] = 89
        with self.assertRaises(ValidationError):
            parse_turn_analysis(payload, KB)

    def test_the_schema_has_nowhere_to_put_a_score(self):
        """The structural guarantee: there is no field for engine-owned data."""
        fields = set(TurnAnalysis.model_fields) | set(ProfileExtraction.model_fields)
        for forbidden in (
            "score",
            "match_score",
            "career_score",
            "courses",
            "skill_gaps",
            "gaps",
            "ranking",
            "recommendations",
        ):
            self.assertNotIn(forbidden, fields)


class TestVocabularyFiltering(unittest.TestCase):
    """The values themselves must exist in the knowledge base."""

    def test_an_invented_skill_is_dropped_not_raised(self):
        result = parse_turn_analysis(
            analysis(
                skills=[("pytorch", 0.9, "explicit"), ("python", 0.8, "explicit")]
            ),
            KB,
        )
        kept = [s.skill_id for s in result.analysis.profile_updates.skills]
        self.assertEqual(kept, ["python"])
        self.assertTrue(result.had_rejections)
        self.assertIn("skill_id='pytorch'", result.rejected)

    def test_an_invented_interest_tag_is_dropped(self):
        result = parse_turn_analysis(
            analysis(interests=[("robotics", "explicit"), ("ai_ml", "explicit")]), KB
        )
        kept = [t.tag for t in result.analysis.profile_updates.interests]
        self.assertEqual(kept, ["ai_ml"])
        self.assertIn("interest='robotics'", result.rejected)

    def test_an_invented_work_preference_is_dropped(self):
        result = parse_turn_analysis(
            analysis(work_preferences=[("vibes", "inferred"), ("building", "explicit")]),
            KB,
        )
        kept = [t.tag for t in result.analysis.profile_updates.work_preferences]
        self.assertEqual(kept, ["building"])

    def test_an_invented_career_is_dropped(self):
        result = parse_turn_analysis(
            analysis(careers=["quantum_alchemist", "ml_engineer"]), KB
        )
        self.assertEqual(result.analysis.careers_mentioned, ["ml_engineer"])
        self.assertIn("career_id='quantum_alchemist'", result.rejected)

    def test_invented_removals_are_dropped(self):
        result = parse_turn_analysis(
            analysis(removed_interests=["robotics", "data"], removed_skills=["pytorch"]),
            KB,
        )
        updates = result.analysis.profile_updates
        self.assertEqual(updates.removed_interests, ["data"])
        self.assertEqual(updates.removed_skills, [])

    def test_duplicates_collapse(self):
        result = parse_turn_analysis(
            analysis(
                skills=[("python", 0.5, "inferred"), ("python", 0.8, "explicit")],
                interests=[("data", "explicit"), ("data", "inferred")],
            ),
            KB,
        )
        updates = result.analysis.profile_updates
        self.assertEqual([s.skill_id for s in updates.skills], ["python"])
        # first wins, so the filter never silently reorders confidence
        self.assertEqual(updates.skills[0].proficiency, 0.5)
        self.assertEqual([t.tag for t in updates.interests], ["data"])

    def test_a_clean_payload_is_returned_unchanged(self):
        original = TurnAnalysis.model_validate(
            analysis(skills=[("sql", 0.6, "explicit")], interests=[("data", "explicit")])
        )
        result = filter_to_vocabulary(original, KB)
        self.assertFalse(result.had_rejections)
        self.assertEqual(result.analysis, original)


class TestGeneratedSchema(unittest.TestCase):
    """The schema handed to the provider closes off invalid values at the source."""

    def setUp(self):
        self.schema = turn_analysis_schema(KB)
        self.updates = self.schema["properties"]["profile_updates"]["properties"]

    def test_skill_ids_are_an_enum_of_the_taxonomy(self):
        enum = self.updates["skills"]["items"]["properties"]["skill_id"]["enum"]
        self.assertEqual(set(enum), set(KB.skills))
        self.assertNotIn("pytorch", enum)

    def test_tag_enums_come_from_the_knowledge_base(self):
        self.assertEqual(
            self.updates["interests"]["items"]["properties"]["tag"]["enum"],
            list(KB.interest_tags),
        )
        self.assertEqual(
            self.updates["work_preferences"]["items"]["properties"]["tag"]["enum"],
            list(KB.work_tags),
        )

    def test_proficiency_is_bounded_in_the_schema(self):
        proficiency = self.updates["skills"]["items"]["properties"]["proficiency"]
        self.assertEqual(proficiency["minimum"], 0.0)
        self.assertEqual(proficiency["maximum"], 1.0)

    def test_every_object_forbids_extra_properties(self):
        """No unlisted key can appear anywhere, at any depth."""

        def walk(node, path="root"):
            if isinstance(node, dict):
                if node.get("type") == "object" or "object" in (node.get("type") or []):
                    self.assertIs(
                        node.get("additionalProperties"), False, f"{path} allows extras"
                    )
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(self.schema)

    def test_source_is_required_on_every_extracted_fact(self):
        for field in ("skills", "interests", "work_preferences", "strengths", "goals"):
            with self.subTest(field=field):
                self.assertIn("source", self.updates[field]["items"]["required"])

    def test_experience_may_be_null(self):
        """Unknown experience is a real state, so the schema has to allow it."""
        self.assertIn("null", self.updates["experience"]["type"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
