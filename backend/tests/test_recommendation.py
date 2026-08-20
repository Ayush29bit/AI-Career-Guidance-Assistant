"""Tests for the deterministic career recommendation engine.

Two kinds of fixture are used deliberately:

  * a tiny synthetic knowledge base, so scoring arithmetic can be checked against
    numbers worked out by hand rather than against whatever the real YAML
    happens to contain today;
  * the real knowledge base, so the engine is also exercised against the data it
    will actually run on.
"""

from __future__ import annotations

import unittest

from app.knowledge.loader import Career, KnowledgeBase, RequiredSkill, Skill, load_knowledge_base
from app.recommendation.engine import (
    CONCERN_ATTAINMENT,
    MIN_INTERESTS,
    MIN_SKILLS,
    MIN_WORK_PREFERENCES,
    STRENGTH_ATTAINMENT,
    UNKNOWN_EXPERIENCE_FIT,
    WEIGHT_EXPERIENCE,
    WEIGHT_INTEREST,
    WEIGHT_PREFERENCE,
    WEIGHT_SKILL,
    calculate_skill_gaps,
    experience_fit_score,
    has_enough_information,
    missing_information,
    recommend_careers,
    score_career,
    skill_attainment,
    skill_match_score,
    tag_match_score,
)
from app.recommendation.profile import (
    InvalidProfileError,
    StudentProfile,
    build_profile,
    validate_profile,
)


def synthetic_kb() -> KnowledgeBase:
    """A hand-checkable knowledge base: two skills, one human skill, one career."""
    skills = {
        "python": Skill("python", "Python", "programming", "language", ("Python Programming",), ()),
        "sql": Skill("sql", "SQL", "programming", "language", ("SQL",), ()),
        "communication": Skill("communication", "Communication", "human", "human", ("Communication",), ()),
    }
    careers = {
        "tester": Career(
            id="tester",
            name="Tester",
            short_description="A synthetic career used by the tests.",
            expected_experience="intermediate",
            interest_tags=("data", "software"),
            work_tags=("building", "analysis"),
            required_skills=(
                RequiredSkill("python", importance=1.0, required_level=0.8),
                RequiredSkill("sql", importance=0.5, required_level=0.6),
                RequiredSkill("communication", importance=0.2, required_level=0.5),
            ),
        )
    }
    return KnowledgeBase(
        skills=skills,
        careers=careers,
        out_of_scope_skills=frozenset(),
        categories=("programming", "human"),
        kinds=("language", "human"),
        interest_tags=("data", "software", "ai_ml", "design"),
        work_tags=("building", "analysis", "creativity"),
        experience_levels=("beginner", "intermediate", "advanced"),
        alias_map={},
    )


class TestWeights(unittest.TestCase):
    def test_weights_match_the_documented_formula(self):
        self.assertEqual(WEIGHT_SKILL, 0.50)
        self.assertEqual(WEIGHT_INTEREST, 0.25)
        self.assertEqual(WEIGHT_PREFERENCE, 0.15)
        self.assertEqual(WEIGHT_EXPERIENCE, 0.10)

    def test_weights_sum_to_one(self):
        total = WEIGHT_SKILL + WEIGHT_INTEREST + WEIGHT_PREFERENCE + WEIGHT_EXPERIENCE
        self.assertAlmostEqual(total, 1.0)


class TestSkillMatching(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()
        self.career = self.kb.careers["tester"]

    def test_attainment_is_proportional_below_the_requirement(self):
        requirement = RequiredSkill("python", importance=1.0, required_level=0.8)
        self.assertAlmostEqual(skill_attainment(0.4, requirement), 0.5)
        self.assertAlmostEqual(skill_attainment(0.2, requirement), 0.25)

    def test_attainment_is_capped_at_the_requirement(self):
        """Exceeding what a career needs earns no extra credit."""
        requirement = RequiredSkill("python", importance=1.0, required_level=0.8)
        self.assertEqual(skill_attainment(0.8, requirement), 1.0)
        self.assertEqual(skill_attainment(1.0, requirement), 1.0)

    def test_attainment_of_a_zero_requirement(self):
        requirement = RequiredSkill("python", importance=1.0, required_level=0.0)
        self.assertEqual(skill_attainment(0.0, requirement), 1.0)

    def test_missing_skill_counts_as_zero_not_skipped(self):
        profile = StudentProfile(skills={})
        self.assertEqual(skill_match_score(profile, self.career), 0.0)

    def test_partial_skill_match_is_importance_weighted(self):
        """Hand-checked: python 1.0*1.0 + sql 0.5*0.5 + comms 0.2*0 over 1.7."""
        profile = StudentProfile(skills={"python": 0.8, "sql": 0.3})
        expected = (1.0 * 1.0 + 0.5 * 0.5 + 0.2 * 0.0) / 1.7
        self.assertAlmostEqual(skill_match_score(profile, self.career), expected)

    def test_full_skill_match(self):
        profile = StudentProfile(skills={"python": 0.8, "sql": 0.6, "communication": 0.5})
        self.assertAlmostEqual(skill_match_score(profile, self.career), 1.0)

    def test_importance_actually_changes_the_result(self):
        """The high-importance skill must move the score more than the low one."""
        only_important = StudentProfile(skills={"python": 0.8})
        only_minor = StudentProfile(skills={"communication": 0.5})
        self.assertGreater(
            skill_match_score(only_important, self.career),
            skill_match_score(only_minor, self.career),
        )

    def test_unknown_skills_in_the_profile_do_not_inflate_the_score(self):
        without = StudentProfile(skills={"python": 0.8})
        with_extra = StudentProfile(skills={"python": 0.8, "communication": 0.0})
        self.assertAlmostEqual(
            skill_match_score(without, self.career), skill_match_score(with_extra, self.career)
        )


class TestTagMatching(unittest.TestCase):
    def test_exact_overlap_as_fraction_of_career_tags(self):
        self.assertAlmostEqual(tag_match_score(("data", "software"), ("data", "software")), 1.0)
        self.assertAlmostEqual(tag_match_score(("data",), ("data", "software")), 0.5)
        self.assertAlmostEqual(tag_match_score((), ("data", "software")), 0.0)

    def test_extra_student_tags_do_not_inflate_the_score(self):
        self.assertAlmostEqual(
            tag_match_score(("data", "design", "ai_ml"), ("data", "software")), 0.5
        )

    def test_career_without_tags_scores_zero(self):
        self.assertEqual(tag_match_score(("data",), ()), 0.0)

    def test_matching_is_exact_not_fuzzy(self):
        """A near-miss string must not match: these are closed vocabularies."""
        self.assertEqual(tag_match_score(("Data",), ("data",)), 0.0)
        self.assertEqual(tag_match_score(("data_science",), ("data",)), 0.0)


class TestExperienceFit(unittest.TestCase):
    def test_exact_match_is_a_full_fit(self):
        self.assertEqual(experience_fit_score("intermediate", "intermediate"), (1.0, True))

    def test_being_above_the_expected_level_is_not_penalised(self):
        self.assertEqual(experience_fit_score("advanced", "beginner"), (1.0, True))
        self.assertEqual(experience_fit_score("advanced", "intermediate"), (1.0, True))

    def test_falling_short_costs_progressively(self):
        one_short = experience_fit_score("beginner", "intermediate")
        two_short = experience_fit_score("beginner", "advanced")
        self.assertEqual(one_short, (0.6, True))
        self.assertEqual(two_short, (0.3, True))
        self.assertGreater(one_short[0], two_short[0])

    def test_unknown_experience_is_neutral_and_flagged(self):
        fit, known = experience_fit_score(None, "intermediate")
        self.assertEqual(fit, UNKNOWN_EXPERIENCE_FIT)
        self.assertFalse(known)

    def test_all_level_pairs_are_defined(self):
        levels = ("beginner", "intermediate", "advanced")
        for student in levels:
            for career in levels:
                fit, known = experience_fit_score(student, career)
                self.assertTrue(known)
                self.assertGreaterEqual(fit, 0.0)
                self.assertLessEqual(fit, 1.0)


class TestCareerScoring(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()
        self.career = self.kb.careers["tester"]

    def test_score_is_the_documented_weighted_sum(self):
        profile = StudentProfile(
            skills={"python": 0.8, "sql": 0.3},
            interests=("data",),
            work_preferences=("building", "analysis"),
            experience_level="intermediate",
        )
        match = score_career(profile, self.career, self.kb)

        expected_skill = (1.0 * 1.0 + 0.5 * 0.5 + 0.2 * 0.0) / 1.7
        expected = (
            0.50 * expected_skill + 0.25 * 0.5 + 0.15 * 1.0 + 0.10 * 1.0
        )
        self.assertAlmostEqual(match.breakdown.skill_match, expected_skill)
        self.assertAlmostEqual(match.breakdown.interest_match, 0.5)
        self.assertAlmostEqual(match.breakdown.preference_match, 1.0)
        self.assertAlmostEqual(match.breakdown.experience_fit, 1.0)
        self.assertAlmostEqual(match.score, expected)

    def test_contributions_sum_to_the_score(self):
        profile = StudentProfile(
            skills={"python": 0.5},
            interests=("data",),
            work_preferences=("building",),
            experience_level="beginner",
        )
        match = score_career(profile, self.career, self.kb)
        total = (
            match.breakdown.skill_contribution
            + match.breakdown.interest_contribution
            + match.breakdown.preference_contribution
            + match.breakdown.experience_contribution
        )
        self.assertAlmostEqual(total, match.score)

    def test_perfect_profile_scores_one(self):
        profile = StudentProfile(
            skills={"python": 1.0, "sql": 1.0, "communication": 1.0},
            interests=("data", "software"),
            work_preferences=("building", "analysis"),
            experience_level="intermediate",
        )
        match = score_career(profile, self.career, self.kb)
        self.assertAlmostEqual(match.score, 1.0)
        self.assertEqual(match.score_100, 100)

    def test_empty_profile_scores_only_the_neutral_experience_term(self):
        """Nothing known => 0 skills, 0 tags, and the neutral experience value."""
        match = score_career(StudentProfile(), self.career, self.kb)
        self.assertAlmostEqual(match.score, WEIGHT_EXPERIENCE * UNKNOWN_EXPERIENCE_FIT)
        self.assertFalse(match.breakdown.experience_known)

    def test_score_is_always_within_zero_and_one(self):
        for profile in (
            StudentProfile(),
            StudentProfile(skills={"python": 1.0, "sql": 1.0, "communication": 1.0}),
            StudentProfile(
                skills={"python": 1.0},
                interests=("data", "software"),
                work_preferences=("building", "analysis"),
                experience_level="advanced",
            ),
        ):
            match = score_career(profile, self.career, self.kb)
            self.assertGreaterEqual(match.score, 0.0)
            self.assertLessEqual(match.score, 1.0)

    def test_score_100_is_a_rounded_integer(self):
        profile = StudentProfile(
            skills={"python": 0.8, "sql": 0.6, "communication": 0.5},
            interests=("data", "software"),
            work_preferences=("building", "analysis"),
            experience_level="intermediate",
        )
        match = score_career(profile, self.career, self.kb)
        self.assertIsInstance(match.score_100, int)
        self.assertEqual(match.score_100, round(match.score * 100))

    def test_scoring_is_deterministic(self):
        profile = StudentProfile(
            skills={"python": 0.7, "sql": 0.4},
            interests=("data",),
            work_preferences=("analysis",),
            experience_level="beginner",
        )
        first = score_career(profile, self.career, self.kb)
        second = score_career(profile, self.career, self.kb)
        self.assertEqual(first, second)


class TestExplainability(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()
        self.career = self.kb.careers["tester"]
        self.profile = StudentProfile(
            skills={"python": 0.8, "sql": 0.1},
            interests=("data",),
            work_preferences=("building",),
            experience_level="beginner",
        )
        self.match = score_career(self.profile, self.career, self.kb)

    def test_payload_carries_every_component_score(self):
        breakdown = self.match.breakdown
        for value in (
            breakdown.skill_match,
            breakdown.interest_match,
            breakdown.preference_match,
            breakdown.experience_fit,
        ):
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_met_requirements_appear_as_strengths(self):
        strength_ids = [f.id for f in self.match.strengths if f.kind == "skill"]
        self.assertIn("python", strength_ids)

    def test_unmet_requirements_appear_as_concerns(self):
        concern_ids = [f.id for f in self.match.concerns if f.kind == "skill"]
        self.assertIn("sql", concern_ids)
        self.assertIn("communication", concern_ids)

    def test_matched_and_missing_tags_are_both_reported(self):
        self.assertEqual(self.match.matching_interests, ("data",))
        self.assertEqual(self.match.missing_interests, ("software",))
        self.assertEqual(self.match.matching_preferences, ("building",))
        self.assertEqual(self.match.missing_preferences, ("analysis",))

    def test_experience_shortfall_is_a_concern(self):
        experience_concerns = [f for f in self.match.concerns if f.kind == "experience"]
        self.assertEqual(len(experience_concerns), 1)
        self.assertAlmostEqual(experience_concerns[0].value, 0.6)

    def test_unknown_experience_produces_no_factor_either_way(self):
        profile = StudentProfile(skills={"python": 0.8}, interests=("data",))
        match = score_career(profile, self.career, self.kb)
        kinds = [f.kind for f in match.strengths + match.concerns]
        self.assertNotIn("experience", kinds)

    def test_factors_are_data_not_sentences(self):
        """The engine emits facts; wording is the LLM's job in a later phase."""
        for factor in self.match.strengths + self.match.concerns:
            self.assertIn(factor.kind, {"skill", "interest", "work_preference", "experience"})
            self.assertNotIn(" ", factor.id)
            self.assertIsInstance(factor.value, float)

    def test_strengths_are_ordered_by_weighted_value(self):
        values = [f.value * f.weight for f in self.match.strengths]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_thresholds_are_respected(self):
        for factor in self.match.strengths:
            if factor.kind == "skill":
                self.assertGreaterEqual(factor.value, STRENGTH_ATTAINMENT)
        for factor in self.match.concerns:
            if factor.kind == "skill":
                self.assertLess(factor.value, CONCERN_ATTAINMENT)

    def test_gaps_are_included_in_the_payload(self):
        self.assertTrue(self.match.skill_gaps)
        self.assertIn("sql", [g.skill_id for g in self.match.skill_gaps])


class TestSkillGaps(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()
        self.career = self.kb.careers["tester"]

    def test_gap_is_required_minus_student_level(self):
        profile = StudentProfile(skills={"python": 0.3})
        technical, _ = calculate_skill_gaps(profile, self.career, self.kb)
        python_gap = next(g for g in technical if g.skill_id == "python")
        self.assertAlmostEqual(python_gap.gap, 0.5)
        self.assertAlmostEqual(python_gap.required_level, 0.8)
        self.assertAlmostEqual(python_gap.student_level, 0.3)

    def test_only_positive_gaps_are_returned(self):
        profile = StudentProfile(skills={"python": 0.9, "sql": 0.6, "communication": 0.9})
        technical, human = calculate_skill_gaps(profile, self.career, self.kb)
        self.assertEqual(technical, ())
        self.assertEqual(human, ())

    def test_exactly_meeting_the_requirement_is_not_a_gap(self):
        """Boundary: student_level == required_level must not produce a gap."""
        profile = StudentProfile(skills={"python": 0.8, "sql": 0.6, "communication": 0.5})
        technical, human = calculate_skill_gaps(profile, self.career, self.kb)
        self.assertEqual(technical, ())
        self.assertEqual(human, ())

    def test_missing_skill_produces_the_full_gap(self):
        technical, _ = calculate_skill_gaps(StudentProfile(), self.career, self.kb)
        python_gap = next(g for g in technical if g.skill_id == "python")
        self.assertAlmostEqual(python_gap.gap, 0.8)
        self.assertAlmostEqual(python_gap.student_level, 0.0)

    def test_priority_is_gap_times_importance(self):
        profile = StudentProfile(skills={"python": 0.3})
        technical, _ = calculate_skill_gaps(profile, self.career, self.kb)
        python_gap = next(g for g in technical if g.skill_id == "python")
        self.assertAlmostEqual(python_gap.priority, 0.5 * 1.0)

    def test_gaps_are_ranked_by_priority_not_size(self):
        """A smaller gap in a critical skill must outrank a larger unimportant one."""
        career = Career(
            id="c",
            name="C",
            short_description="d",
            expected_experience="beginner",
            interest_tags=("data",),
            work_tags=("building",),
            required_skills=(
                RequiredSkill("python", importance=1.0, required_level=0.6),  # gap .6 prio .60
                RequiredSkill("sql", importance=0.2, required_level=0.9),     # gap .9 prio .18
            ),
        )
        technical, _ = calculate_skill_gaps(StudentProfile(), career, self.kb)
        by_id = {g.skill_id: g for g in technical}
        # sql has the bigger raw gap...
        self.assertGreater(by_id["sql"].gap, by_id["python"].gap)
        # ...but python is more important, so it ranks first
        self.assertEqual([g.skill_id for g in technical], ["python", "sql"])

    def test_human_skills_are_separated_from_technical_gaps(self):
        technical, human = calculate_skill_gaps(StudentProfile(), self.career, self.kb)
        self.assertEqual([g.skill_id for g in human], ["communication"])
        self.assertNotIn("communication", [g.skill_id for g in technical])
        self.assertTrue(all(g.is_human_skill for g in human))
        self.assertFalse(any(g.is_human_skill for g in technical))

    def test_gap_ordering_is_stable(self):
        first, _ = calculate_skill_gaps(StudentProfile(), self.career, self.kb)
        second, _ = calculate_skill_gaps(StudentProfile(), self.career, self.kb)
        self.assertEqual([g.skill_id for g in first], [g.skill_id for g in second])


class TestColdStart(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()

    def test_empty_profile_is_refused(self):
        result = recommend_careers(StudentProfile(), self.kb)
        self.assertEqual(result.status, "insufficient_profile")
        self.assertEqual(result.matches, ())

    def test_refusal_says_what_is_missing(self):
        result = recommend_careers(StudentProfile(), self.kb)
        fields = {m.field_name for m in result.missing_information}
        self.assertEqual(fields, {"skills", "interests", "work_preferences"})
        skills_entry = next(m for m in result.missing_information if m.field_name == "skills")
        self.assertEqual((skills_entry.have, skills_entry.need), (0, MIN_SKILLS))

    def test_thin_profile_is_refused(self):
        profile = StudentProfile(skills={"python": 0.8}, interests=("data",))
        result = recommend_careers(profile, self.kb)
        self.assertEqual(result.status, "insufficient_profile")

    def test_profile_at_the_threshold_is_accepted(self):
        profile = StudentProfile(
            skills={"python": 0.5, "sql": 0.5, "communication": 0.5},
            interests=("data",),
            work_preferences=("building",),
        )
        self.assertTrue(has_enough_information(profile))
        self.assertEqual(recommend_careers(profile, self.kb).status, "ok")

    def test_one_short_of_the_threshold_is_refused(self):
        profile = StudentProfile(
            skills={"python": 0.5, "sql": 0.5},
            interests=("data",),
            work_preferences=("building",),
        )
        self.assertFalse(has_enough_information(profile))
        self.assertEqual(len(missing_information(profile)), 1)

    def test_experience_is_not_required_to_rank(self):
        profile = StudentProfile(
            skills={"python": 0.5, "sql": 0.5, "communication": 0.5},
            interests=("data",),
            work_preferences=("building",),
            experience_level=None,
        )
        self.assertEqual(recommend_careers(profile, self.kb).status, "ok")

    def test_thresholds_are_the_documented_values(self):
        self.assertEqual((MIN_SKILLS, MIN_INTERESTS, MIN_WORK_PREFERENCES), (3, 1, 1))


class TestProfileValidation(unittest.TestCase):
    def setUp(self):
        self.kb = synthetic_kb()

    def test_valid_profile_builds(self):
        profile = build_profile(
            self.kb,
            skills={"python": 0.8},
            interests=["data"],
            work_preferences=["building"],
            experience_level="intermediate",
        )
        self.assertEqual(profile.proficiency("python"), 0.8)

    def test_unknown_skill_id_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, skills={"cobol": 0.5})
        self.assertIn("unknown skill id 'cobol'", str(ctx.exception))

    def test_out_of_range_proficiency_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, skills={"python": 1.5})
        self.assertIn("not in 0..1", str(ctx.exception))

    def test_negative_proficiency_is_rejected(self):
        with self.assertRaises(InvalidProfileError):
            build_profile(self.kb, skills={"python": -0.1})

    def test_boundary_proficiencies_are_accepted(self):
        profile = build_profile(self.kb, skills={"python": 0.0, "sql": 1.0})
        self.assertEqual(profile.proficiency("python"), 0.0)
        self.assertEqual(profile.proficiency("sql"), 1.0)

    def test_non_numeric_proficiency_is_rejected(self):
        with self.assertRaises(InvalidProfileError):
            build_profile(self.kb, skills={"python": "high"})

    def test_unknown_interest_tag_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, interests=["vibes"])
        self.assertIn("unknown interest tag", str(ctx.exception))

    def test_unknown_work_tag_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, work_preferences=["napping"])
        self.assertIn("unknown work preference tag", str(ctx.exception))

    def test_unknown_experience_level_is_rejected(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, experience_level="wizard")
        self.assertIn("unknown experience level", str(ctx.exception))

    def test_duplicate_tags_are_rejected(self):
        with self.assertRaises(InvalidProfileError):
            build_profile(self.kb, interests=["data", "data"])

    def test_all_problems_are_reported_at_once(self):
        with self.assertRaises(InvalidProfileError) as ctx:
            build_profile(self.kb, skills={"cobol": 5.0}, interests=["vibes"])
        self.assertGreaterEqual(len(ctx.exception.problems), 3)

    def test_none_experience_is_valid(self):
        self.assertEqual(validate_profile(StudentProfile(experience_level=None), self.kb), [])

    def test_engine_rejects_an_invalid_profile(self):
        bad = StudentProfile(skills={"cobol": 0.5})  # bypasses build_profile
        with self.assertRaises(InvalidProfileError):
            recommend_careers(bad, self.kb)


class TestRankingAgainstRealKnowledgeBase(unittest.TestCase):
    """The engine must also behave sensibly on the data it will really run on."""

    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def ml_student(self) -> StudentProfile:
        return build_profile(
            self.kb,
            skills={
                "python": 0.85,
                "machine_learning": 0.75,
                "deep_learning": 0.55,
                "statistics": 0.6,
                "mathematics": 0.6,
                "sql": 0.5,
            },
            interests=["ai_ml", "data", "software"],
            work_preferences=["building", "experimentation", "systems_thinking"],
            experience_level="intermediate",
        )

    def design_student(self) -> StudentProfile:
        return build_profile(
            self.kb,
            skills={"ux_design": 0.7, "user_research": 0.6, "visual_design": 0.65, "communication": 0.7},
            interests=["design", "product", "research"],
            work_preferences=["creativity", "visual", "people_facing"],
            experience_level="beginner",
        )

    def test_ml_student_ranks_ml_engineer_first(self):
        result = recommend_careers(self.ml_student(), self.kb)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.matches[0].career_id, "ml_engineer")

    def test_design_student_ranks_ux_designer_first(self):
        result = recommend_careers(self.design_student(), self.kb)
        self.assertEqual(result.matches[0].career_id, "ux_designer")

    def test_different_students_get_different_rankings(self):
        ml = recommend_careers(self.ml_student(), self.kb)
        design = recommend_careers(self.design_student(), self.kb)
        self.assertNotEqual(
            [m.career_id for m in ml.matches], [m.career_id for m in design.matches]
        )

    def test_results_are_sorted_descending(self):
        result = recommend_careers(self.ml_student(), self.kb, limit=None)
        scores = [m.score for m in result.matches]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_limit_is_respected_and_all_careers_are_considered(self):
        result = recommend_careers(self.ml_student(), self.kb, limit=3)
        self.assertEqual(len(result.matches), 3)
        self.assertEqual(result.considered_careers, len(self.kb.careers))

    def test_every_career_is_scored_when_unlimited(self):
        result = recommend_careers(self.ml_student(), self.kb, limit=None)
        self.assertEqual(len(result.matches), len(self.kb.careers))

    def test_ranking_is_deterministic_across_runs(self):
        profile = self.ml_student()
        first = recommend_careers(profile, self.kb, limit=None)
        second = recommend_careers(profile, self.kb, limit=None)
        self.assertEqual(
            [(m.career_id, m.score) for m in first.matches],
            [(m.career_id, m.score) for m in second.matches],
        )

    def test_zero_coverage_skills_still_produce_gaps(self):
        """MLOps has no course, but it must still show up as a real gap."""
        result = recommend_careers(self.ml_student(), self.kb)
        ml_engineer = next(m for m in result.matches if m.career_id == "ml_engineer")
        gap_ids = [g.skill_id for g in ml_engineer.skill_gaps]
        self.assertIn("mlops", gap_ids)
        self.assertIn("model_deployment", gap_ids)

    def test_human_skills_never_appear_in_technical_gaps(self):
        result = recommend_careers(self.ml_student(), self.kb, limit=None)
        for match in result.matches:
            for gap in match.skill_gaps:
                self.assertFalse(self.kb.skills[gap.skill_id].is_human_skill, gap.skill_id)

    def test_scores_stay_in_range_for_every_career(self):
        result = recommend_careers(self.ml_student(), self.kb, limit=None)
        for match in result.matches:
            self.assertGreaterEqual(match.score_100, 0)
            self.assertLessEqual(match.score_100, 100)


if __name__ == "__main__":
    unittest.main()
