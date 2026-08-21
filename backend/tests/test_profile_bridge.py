"""Tests for the database -> StudentProfile bridge.

These need no database. The bridge reads plain attributes off the ORM objects,
so a profile assembled in memory exercises exactly the same code path as one
loaded from PostgreSQL, without the setup cost.

The point of most of these is not "does it copy fields" but "does a profile that
came from the database score identically to the same profile built by hand".
"""

from __future__ import annotations

import unittest

from app.conversation.profile_bridge import (
    DISLIKE_ENTRY,
    GOAL_ENTRY,
    INTEREST_TAG,
    STRENGTH_ENTRY,
    WORK_PREFERENCE_TAG,
    bridge_profile,
    to_student_profile,
)
from app.database import models
from app.knowledge.loader import load_knowledge_base
from app.recommendation.engine import recommend_careers
from app.recommendation.profile import InvalidProfileError, build_profile


def db_profile(
    *,
    experience_level: str | None = None,
    skills: dict[str, float] | None = None,
    interests: tuple[str, ...] = (),
    work_preferences: tuple[str, ...] = (),
    strengths: tuple[str, ...] = (),
    dislikes: tuple[str, ...] = (),
    goals: tuple[str, ...] = (),
) -> models.StudentProfile:
    """An unsaved ORM profile. No session, no database."""
    profile = models.StudentProfile(experience_level=experience_level)
    profile.skills = [
        models.StudentProfileSkill(skill_id=skill_id, proficiency=proficiency)
        for skill_id, proficiency in (skills or {}).items()
    ]
    profile.tags = [
        models.StudentProfileTag(tag_type=INTEREST_TAG, tag=tag) for tag in interests
    ] + [
        models.StudentProfileTag(tag_type=WORK_PREFERENCE_TAG, tag=tag)
        for tag in work_preferences
    ]
    profile.entries = [
        models.StudentProfileEntry(entry_type=entry_type, content=content)
        for entry_type, values in (
            (STRENGTH_ENTRY, strengths),
            (DISLIKE_ENTRY, dislikes),
            (GOAL_ENTRY, goals),
        )
        for content in values
    ]
    return profile


class TestVocabularyConstants(unittest.TestCase):
    """The bridge's literals must stay members of the database vocabularies."""

    def test_tag_types_are_valid(self):
        self.assertIn(INTEREST_TAG, models.TAG_TYPES)
        self.assertIn(WORK_PREFERENCE_TAG, models.TAG_TYPES)
        self.assertNotEqual(INTEREST_TAG, WORK_PREFERENCE_TAG)

    def test_entry_types_are_valid(self):
        for entry_type in (STRENGTH_ENTRY, DISLIKE_ENTRY, GOAL_ENTRY):
            self.assertIn(entry_type, models.PROFILE_ENTRY_TYPES)
        self.assertEqual(len({STRENGTH_ENTRY, DISLIKE_ENTRY, GOAL_ENTRY}), 3)

    def test_bridge_covers_every_entry_type(self):
        """A new entry type in the schema must not be silently ignored here."""
        self.assertEqual(
            {STRENGTH_ENTRY, DISLIKE_ENTRY, GOAL_ENTRY}, set(models.PROFILE_ENTRY_TYPES)
        )

    def test_bridge_covers_every_tag_type(self):
        self.assertEqual({INTEREST_TAG, WORK_PREFERENCE_TAG}, set(models.TAG_TYPES))


class TestConversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_skills_and_proficiency_carry_over(self):
        profile = to_student_profile(
            db_profile(skills={"python": 0.8, "sql": 0.5, "machine_learning": 0.65}), self.kb
        )
        self.assertEqual(
            profile.skills, {"python": 0.8, "sql": 0.5, "machine_learning": 0.65}
        )
        self.assertEqual(profile.proficiency("python"), 0.8)

    def test_absent_skill_is_zero_but_not_known(self):
        profile = to_student_profile(db_profile(skills={"python": 0.8}), self.kb)
        self.assertEqual(profile.proficiency("mlops"), 0.0)
        self.assertFalse(profile.knows("mlops"))
        self.assertTrue(profile.knows("python"))

    def test_a_stored_zero_is_known(self):
        """A stored 0.0 means 'we asked and it is none', not 'we never asked'."""
        profile = to_student_profile(db_profile(skills={"statistics": 0.0}), self.kb)
        self.assertTrue(profile.knows("statistics"))
        self.assertEqual(profile.proficiency("statistics"), 0.0)

    def test_tags_are_split_by_type(self):
        profile = to_student_profile(
            db_profile(interests=("ai_ml", "data"), work_preferences=("building",)), self.kb
        )
        self.assertEqual(set(profile.interests), {"ai_ml", "data"})
        self.assertEqual(profile.work_preferences, ("building",))

    def test_unknown_experience_stays_unknown(self):
        """The engine has a defined neutral for None; guessing would shift scores."""
        profile = to_student_profile(db_profile(experience_level=None), self.kb)
        self.assertIsNone(profile.experience_level)

    def test_known_experience_carries_over(self):
        for level in ("beginner", "intermediate", "advanced"):
            with self.subTest(level=level):
                profile = to_student_profile(db_profile(experience_level=level), self.kb)
                self.assertEqual(profile.experience_level, level)

    def test_goals_reach_the_engine_profile(self):
        profile = to_student_profile(db_profile(goals=("Work on AI products",)), self.kb)
        self.assertEqual(profile.goals, ("Work on AI products",))

    def test_strengths_and_dislikes_are_kept_beside_the_engine_profile(self):
        bridged = bridge_profile(
            db_profile(
                strengths=("Ships side projects",),
                dislikes=("Heavy statistics",),
                goals=("Work on AI products",),
            ),
            self.kb,
        )
        self.assertEqual(bridged.strengths, ("Ships side projects",))
        self.assertEqual(bridged.dislikes, ("Heavy statistics",))
        self.assertEqual(bridged.goals, ("Work on AI products",))
        # they must not leak into anything the engine scores
        self.assertEqual(bridged.profile.skills, {})
        self.assertEqual(bridged.profile.interests, ())

    def test_entry_types_do_not_bleed_into_each_other(self):
        bridged = bridge_profile(
            db_profile(strengths=("a",), dislikes=("b",), goals=("c",)), self.kb
        )
        self.assertEqual(bridged.strengths, ("a",))
        self.assertEqual(bridged.dislikes, ("b",))
        self.assertEqual(bridged.goals, ("c",))

    def test_entry_order_is_preserved(self):
        bridged = bridge_profile(db_profile(goals=("first", "second", "third")), self.kb)
        self.assertEqual(bridged.goals, ("first", "second", "third"))

    def test_empty_profile_converts_to_an_empty_profile(self):
        profile = to_student_profile(db_profile(), self.kb)
        self.assertEqual(profile.skills, {})
        self.assertEqual(profile.interests, ())
        self.assertEqual(profile.work_preferences, ())
        self.assertIsNone(profile.experience_level)
        self.assertEqual(profile.known_signal_count, 0)

    def test_conversion_is_deterministic(self):
        source = db_profile(
            skills={"sql": 0.5, "python": 0.8},
            interests=("data", "ai_ml"),
            work_preferences=("building", "analysis"),
        )
        first = to_student_profile(source, self.kb)
        second = to_student_profile(source, self.kb)
        self.assertEqual(first, second)


class TestValidation(unittest.TestCase):
    """Bad stored data must fail loudly, never be silently dropped or guessed."""

    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_unknown_skill_id_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as caught:
            to_student_profile(db_profile(skills={"underwater_welding": 0.5}), self.kb)
        self.assertIn("underwater_welding", str(caught.exception))

    def test_unknown_interest_tag_is_rejected(self):
        with self.assertRaises(InvalidProfileError):
            to_student_profile(db_profile(interests=("basket_weaving",)), self.kb)

    def test_unknown_work_preference_is_rejected(self):
        with self.assertRaises(InvalidProfileError):
            to_student_profile(db_profile(work_preferences=("telepathy",)), self.kb)

    def test_out_of_range_proficiency_is_rejected(self):
        with self.assertRaises(InvalidProfileError):
            to_student_profile(db_profile(skills={"python": 1.5}), self.kb)

    def test_every_problem_is_reported_at_once(self):
        with self.assertRaises(InvalidProfileError) as caught:
            to_student_profile(
                db_profile(skills={"nonsense": 0.5}, interests=("nonsense_tag",)), self.kb
            )
        self.assertGreaterEqual(len(caught.exception.problems), 2)


class TestSemanticsArePreserved(unittest.TestCase):
    """A bridged profile must behave exactly like a hand-built one."""

    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()
        cls.skills = {"python": 0.8, "machine_learning": 0.7, "sql": 0.6, "statistics": 0.4}
        cls.interests = ("ai_ml", "data")
        cls.preferences = ("building", "experimentation")

    def _hand_built(self, experience_level: str | None):
        return build_profile(
            self.kb,
            skills=dict(self.skills),
            interests=self.interests,
            work_preferences=self.preferences,
            experience_level=experience_level,
        )

    def _bridged(self, experience_level: str | None):
        return to_student_profile(
            db_profile(
                skills=dict(self.skills),
                interests=self.interests,
                work_preferences=self.preferences,
                experience_level=experience_level,
            ),
            self.kb,
        )

    def test_bridged_profile_equals_hand_built_profile(self):
        for level in (None, "beginner", "intermediate", "advanced"):
            with self.subTest(experience_level=level):
                self.assertEqual(self._bridged(level), self._hand_built(level))

    def test_bridged_profile_produces_identical_recommendations(self):
        for level in (None, "intermediate"):
            with self.subTest(experience_level=level):
                from_db = recommend_careers(self._bridged(level), self.kb)
                by_hand = recommend_careers(self._hand_built(level), self.kb)
                self.assertEqual(
                    [(m.career_id, m.score) for m in from_db.matches],
                    [(m.career_id, m.score) for m in by_hand.matches],
                )

    def test_unknown_experience_scores_neutrally_not_as_beginner(self):
        """Guessing an experience level would change every career score."""
        unknown = recommend_careers(self._bridged(None), self.kb)
        beginner = recommend_careers(self._bridged("beginner"), self.kb)
        self.assertNotEqual(
            [m.score for m in unknown.matches], [m.score for m in beginner.matches]
        )
        self.assertFalse(unknown.matches[0].breakdown.experience_known)
        self.assertTrue(beginner.matches[0].breakdown.experience_known)

    def test_cold_start_survives_the_bridge(self):
        thin = to_student_profile(db_profile(skills={"python": 0.8}), self.kb)
        result = recommend_careers(thin, self.kb)
        self.assertEqual(result.status, "insufficient_profile")
        self.assertEqual(result.matches, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
