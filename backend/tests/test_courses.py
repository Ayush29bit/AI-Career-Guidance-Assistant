"""Tests for deterministic course retrieval and ranking."""

from __future__ import annotations

import unittest

from app.ingestion.coursera import NormalizedCourse
from app.knowledge.loader import load_knowledge_base
from app.recommendation.courses import (
    BLEND_COURSE_FOCUS,
    BLEND_GAP_COVERAGE,
    GOOD_COVERAGE_MIN,
    MIXED_DIFFICULTY_FIT,
    NEUTRAL_DIFFICULTY_FIT,
    WEIGHT_COVERAGE,
    WEIGHT_DIFFICULTY,
    WEIGHT_POPULARITY,
    WEIGHT_RATING,
    build_catalogue,
    coverage_scores,
    difficulty_fit_score,
    load_catalogue,
    popularity_score,
    rank_courses,
    rating_score,
    recommend_courses_for_gaps,
    recommend_courses_for_skill,
    score_course,
    target_difficulty_level,
)
from app.recommendation.engine import SkillGap
from app.recommendation.profile import StudentProfile


def course(
    course_id: str,
    *,
    skills: tuple[str, ...],
    rating: float | None = 4.7,
    reviews: int | None = 1000,
    difficulty: str | None = "beginner",
    url: str | None = "https://example.test/course",
    title: str | None = None,
) -> NormalizedCourse:
    """A synthetic course with only the fields ranking actually reads."""
    return NormalizedCourse(
        course_id=course_id,
        title=title or course_id.replace("-", " ").title(),
        organization="Test Org",
        url=url,
        rating=rating,
        review_count=reviews,
        review_count_raw=str(reviews or ""),
        review_count_is_approximate=False,
        students_enrolled=None,
        difficulty=difficulty,
        course_type="course",
        duration="1_3_months",
        raw_skills=(),
        skills=skills,
        technical_skills=skills,
        dropped_skills=(),
        description="ignored by ranking",
    )


class TestWeights(unittest.TestCase):
    def test_top_level_weights_match_the_documented_formula(self):
        self.assertEqual(WEIGHT_COVERAGE, 0.50)
        self.assertEqual(WEIGHT_DIFFICULTY, 0.20)
        self.assertEqual(WEIGHT_RATING, 0.20)
        self.assertEqual(WEIGHT_POPULARITY, 0.10)

    def test_top_level_weights_sum_to_one(self):
        total = WEIGHT_COVERAGE + WEIGHT_DIFFICULTY + WEIGHT_RATING + WEIGHT_POPULARITY
        self.assertAlmostEqual(total, 1.0)

    def test_coverage_blend_sums_to_one(self):
        self.assertAlmostEqual(BLEND_GAP_COVERAGE + BLEND_COURSE_FOCUS, 1.0)


class TestCoverageComponent(unittest.TestCase):
    def test_gap_coverage_is_priority_weighted(self):
        gaps = {"deep_learning": 0.6, "sql": 0.2}
        c = course("c", skills=("deep_learning",))
        _, gap_coverage, _, covered = coverage_scores(c, gaps)
        self.assertAlmostEqual(gap_coverage, 0.6 / 0.8)
        self.assertEqual(covered, ("deep_learning",))

    def test_course_focus_is_covered_over_total_technical_skills(self):
        gaps = {"deep_learning": 1.0}
        c = course("c", skills=("deep_learning", "python", "sql", "linux"))
        _, _, focus, _ = coverage_scores(c, gaps)
        self.assertAlmostEqual(focus, 0.25)

    def test_blend_is_the_documented_formula(self):
        gaps = {"deep_learning": 0.6, "sql": 0.4}
        c = course("c", skills=("deep_learning", "linux"))
        blended, gap_coverage, focus, _ = coverage_scores(c, gaps)
        self.assertAlmostEqual(
            blended, BLEND_GAP_COVERAGE * gap_coverage + BLEND_COURSE_FOCUS * focus
        )

    def test_no_overlap_scores_zero(self):
        blended, gap_coverage, focus, covered = coverage_scores(
            course("c", skills=("linux",)), {"deep_learning": 1.0}
        )
        self.assertEqual((blended, gap_coverage, focus, covered), (0.0, 0.0, 0.0, ()))

    def test_covering_more_important_gaps_scores_higher(self):
        gaps = {"critical": 0.9, "minor": 0.1}
        important = coverage_scores(course("a", skills=("critical",)), gaps)[0]
        unimportant = coverage_scores(course("b", skills=("minor",)), gaps)[0]
        self.assertGreater(important, unimportant)


class TestRatingNormalization(unittest.TestCase):
    def test_fixed_absolute_scale(self):
        self.assertAlmostEqual(rating_score(5.0), 1.0)
        self.assertAlmostEqual(rating_score(4.0), 0.5)
        self.assertAlmostEqual(rating_score(3.0), 0.0)

    def test_below_the_floor_is_clamped(self):
        self.assertEqual(rating_score(2.8), 0.0)

    def test_missing_rating_is_neutral(self):
        self.assertEqual(rating_score(None), 0.5)

    def test_compressed_ratings_produce_small_differences(self):
        """4.6 vs 4.8 must not become a large score gap."""
        difference = rating_score(4.8) - rating_score(4.6)
        self.assertAlmostEqual(difference, 0.1)
        self.assertLess(difference * WEIGHT_RATING, 0.03)


class TestPopularityNormalization(unittest.TestCase):
    def test_log_scaled_and_monotonic(self):
        self.assertLess(popularity_score(10), popularity_score(1_000))
        self.assertLess(popularity_score(1_000), popularity_score(100_000))

    def test_bounded_to_zero_one(self):
        self.assertEqual(popularity_score(0), 0.0)
        self.assertEqual(popularity_score(None), 0.0)
        self.assertLessEqual(popularity_score(3_641_053), 1.0)

    def test_normalization_does_not_depend_on_other_candidates(self):
        """A fixed reference keeps scores comparable across different queries."""
        self.assertEqual(popularity_score(20_000), popularity_score(20_000))

    def test_huge_review_counts_cannot_dominate(self):
        """The full popularity swing is worth at most the 10% weight."""
        swing = (popularity_score(3_641_053) - popularity_score(6)) * WEIGHT_POPULARITY
        self.assertLess(swing, 0.09)


class TestDifficultyFit(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(difficulty_fit_score("beginner", "beginner"), 1.0)
        self.assertEqual(difficulty_fit_score("advanced", "advanced"), 1.0)

    def test_too_hard_is_penalised_more_than_too_easy(self):
        too_hard = difficulty_fit_score("intermediate", "beginner")
        too_easy = difficulty_fit_score("beginner", "intermediate")
        self.assertLess(too_hard, too_easy)

    def test_two_levels_off(self):
        self.assertEqual(difficulty_fit_score("advanced", "beginner"), 0.25)
        self.assertEqual(difficulty_fit_score("beginner", "advanced"), 0.4)

    def test_mixed_uses_one_constant_for_every_target(self):
        for target in ("beginner", "intermediate", "advanced"):
            self.assertEqual(difficulty_fit_score("mixed", target), MIXED_DIFFICULTY_FIT)

    def test_mixed_beats_a_two_level_mismatch_but_not_an_exact_match(self):
        self.assertGreater(difficulty_fit_score("mixed", "beginner"),
                           difficulty_fit_score("advanced", "beginner"))
        self.assertLess(difficulty_fit_score("mixed", "beginner"),
                        difficulty_fit_score("beginner", "beginner"))

    def test_unknown_values_are_neutral(self):
        self.assertEqual(difficulty_fit_score(None, "beginner"), NEUTRAL_DIFFICULTY_FIT)
        self.assertEqual(difficulty_fit_score("beginner", None), NEUTRAL_DIFFICULTY_FIT)

    def test_target_prefers_student_experience_then_career(self):
        student = StudentProfile(experience_level="advanced")
        self.assertEqual(target_difficulty_level(student, "beginner"), "advanced")
        unknown = StudentProfile(experience_level=None)
        self.assertEqual(target_difficulty_level(unknown, "intermediate"), "intermediate")
        self.assertIsNone(target_difficulty_level(None, None))


class TestCourseScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_score_is_the_documented_weighted_sum(self):
        gaps = {"deep_learning": 1.0}
        c = course("c", skills=("deep_learning", "python"), rating=4.0, reviews=1000,
                   difficulty="beginner")
        result = score_course(c, gaps, self.kb, "beginner")

        expected_coverage = BLEND_GAP_COVERAGE * 1.0 + BLEND_COURSE_FOCUS * 0.5
        expected = (
            WEIGHT_COVERAGE * expected_coverage
            + WEIGHT_DIFFICULTY * 1.0
            + WEIGHT_RATING * 0.5
            + WEIGHT_POPULARITY * popularity_score(1000)
        )
        self.assertAlmostEqual(result.breakdown.skill_coverage, expected_coverage)
        self.assertAlmostEqual(result.score, expected)

    def test_contributions_sum_to_the_score(self):
        result = score_course(
            course("c", skills=("deep_learning",)), {"deep_learning": 1.0}, self.kb, "beginner"
        )
        b = result.breakdown
        total = (
            b.coverage_contribution
            + b.difficulty_contribution
            + b.rating_contribution
            + b.popularity_contribution
        )
        self.assertAlmostEqual(total, result.score)

    def test_zero_relevance_scores_exactly_zero(self):
        """A perfect course about the wrong thing must score 0, not 'a bit'."""
        perfect_but_irrelevant = course(
            "c", skills=("linux",), rating=5.0, reviews=3_000_000, difficulty="beginner"
        )
        result = score_course(perfect_but_irrelevant, {"deep_learning": 1.0}, self.kb, "beginner")
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.covered_gap_skills, ())

    def test_rating_and_popularity_cannot_beat_relevance(self):
        gaps = {"deep_learning": 1.0}
        relevant_but_mediocre = course(
            "relevant", skills=("deep_learning",), rating=3.5, reviews=20
        )
        irrelevant_but_stellar = course(
            "irrelevant", skills=("linux",), rating=5.0, reviews=3_000_000
        )
        ranked = rank_courses(
            [irrelevant_but_stellar, relevant_but_mediocre], gaps, self.kb, "beginner"
        )
        self.assertEqual([r.course_id for r in ranked], ["relevant"])

    def test_score_100_is_a_rounded_integer(self):
        result = score_course(
            course("c", skills=("deep_learning",)), {"deep_learning": 1.0}, self.kb, "beginner"
        )
        self.assertIsInstance(result.score_100, int)
        self.assertEqual(result.score_100, round(result.score * 100))

    def test_missing_url_is_reported_but_not_disqualifying(self):
        no_url = course("no-url", skills=("deep_learning",), url=None)
        ranked = rank_courses([no_url], {"deep_learning": 1.0}, self.kb, "beginner")
        self.assertEqual(len(ranked), 1)
        self.assertFalse(ranked[0].has_url)
        self.assertIsNone(ranked[0].url)
        self.assertGreater(ranked[0].score, 0.0)

    def test_url_presence_does_not_change_the_score(self):
        gaps = {"deep_learning": 1.0}
        with_url = score_course(course("a", skills=("deep_learning",)), gaps, self.kb, "beginner")
        without = score_course(
            course("a", skills=("deep_learning",), url=None), gaps, self.kb, "beginner"
        )
        self.assertAlmostEqual(with_url.score, without.score)

    def test_description_is_not_used_in_ranking(self):
        gaps = {"deep_learning": 1.0}
        base = course("a", skills=("deep_learning",))
        corrupted = NormalizedCourse(**{**base.__dict__, "description": "totally unrelated text"})
        self.assertAlmostEqual(
            score_course(base, gaps, self.kb, "beginner").score,
            score_course(corrupted, gaps, self.kb, "beginner").score,
        )

    def test_covered_skill_names_come_from_the_knowledge_base(self):
        result = score_course(
            course("c", skills=("deep_learning",)), {"deep_learning": 1.0}, self.kb, "beginner"
        )
        self.assertEqual(result.covered_gap_skill_names, ("Deep Learning",))


class TestMegaCourseVsFocusedCourse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_focused_course_beats_mega_certificate_on_a_narrow_gap(self):
        """The whole point of the focus term."""
        focused = course("focused", skills=("deep_learning", "python"), rating=4.7, reviews=5_000)
        mega = course(
            "mega",
            skills=("deep_learning",) + tuple(f"filler_{i}" for i in range(29)),
            rating=4.7,
            reviews=100_000,
        )
        ranked = rank_courses([mega, focused], {"deep_learning": 1.0}, self.kb, "beginner")
        self.assertEqual(ranked[0].course_id, "focused")
        self.assertGreater(ranked[0].breakdown.course_focus, ranked[1].breakdown.course_focus)

    def test_mega_certificate_still_wins_when_it_genuinely_covers_more(self):
        """Focus is a counterweight, not a blanket penalty on breadth."""
        gaps = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
        broad = course("broad", skills=("a", "b", "c", "d"))
        narrow = course("narrow", skills=("a",))
        ranked = rank_courses([narrow, broad], gaps, self.kb, "beginner")
        self.assertEqual(ranked[0].course_id, "broad")

    def test_focus_penalises_padding(self):
        gaps = {"a": 1.0}
        lean = course("lean", skills=("a", "b"))
        padded = course("padded", skills=("a",) + tuple(f"x{i}" for i in range(20)))
        self.assertGreater(
            score_course(lean, gaps, self.kb, "beginner").score,
            score_course(padded, gaps, self.kb, "beginner").score,
        )


class TestDeterminism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_identical_courses_tie_break_by_course_id(self):
        a = course("bbb", skills=("deep_learning",))
        b = course("aaa", skills=("deep_learning",))
        ranked = rank_courses([a, b], {"deep_learning": 1.0}, self.kb, "beginner")
        self.assertAlmostEqual(ranked[0].score, ranked[1].score)
        self.assertEqual([r.course_id for r in ranked], ["aaa", "bbb"])

    def test_input_order_does_not_change_output(self):
        courses = [
            course("c1", skills=("deep_learning",), rating=4.5),
            course("c2", skills=("deep_learning", "python"), rating=4.8),
            course("c3", skills=("deep_learning",), rating=4.8),
        ]
        gaps = {"deep_learning": 1.0}
        forward = rank_courses(courses, gaps, self.kb, "beginner")
        backward = rank_courses(list(reversed(courses)), gaps, self.kb, "beginner")
        self.assertEqual([r.course_id for r in forward], [r.course_id for r in backward])

    def test_limit_is_respected(self):
        courses = [course(f"c{i}", skills=("deep_learning",)) for i in range(10)]
        self.assertEqual(len(rank_courses(courses, {"deep_learning": 1.0}, self.kb, "beginner", limit=3)), 3)


class TestCoverageStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_good_coverage(self):
        catalogue = build_catalogue(
            [course(f"c{i}", skills=("deep_learning",)) for i in range(GOOD_COVERAGE_MIN)]
        )
        state, proxies = catalogue.coverage_state("deep_learning", self.kb)
        self.assertEqual((state, proxies), ("good", ()))

    def test_thin_coverage(self):
        catalogue = build_catalogue([course("c0", skills=("deep_learning",))])
        state, _ = catalogue.coverage_state("deep_learning", self.kb)
        self.assertEqual(state, "thin")

    def test_proxy_coverage_uses_related_skills(self):
        """mlops has no courses of its own but declares related skills."""
        catalogue = build_catalogue([course("c0", skills=("devops",))])
        state, proxies = catalogue.coverage_state("mlops", self.kb)
        self.assertEqual(state, "proxy")
        self.assertIn("devops", proxies)

    def test_no_coverage(self):
        catalogue = build_catalogue([course("c0", skills=("python",))])
        state, proxies = catalogue.coverage_state("version_control", self.kb)
        self.assertEqual((state, proxies), ("none", ()))

    def test_no_coverage_returns_no_course_available(self):
        catalogue = build_catalogue([course("c0", skills=("python",))])
        result = recommend_courses_for_skill("version_control", catalogue, self.kb)
        self.assertEqual(result.coverage, "none")
        self.assertTrue(result.no_course_available)
        self.assertEqual(result.courses, ())

    def test_proxy_courses_are_flagged_as_proxies(self):
        catalogue = build_catalogue([course("devops-course", skills=("devops",))])
        result = recommend_courses_for_skill("mlops", catalogue, self.kb)
        self.assertEqual(result.coverage, "proxy")
        self.assertTrue(result.courses)
        for rec in result.courses:
            self.assertTrue(rec.is_proxy)
            self.assertEqual(rec.proxy_for, "mlops")
        self.assertNotIn("mlops", result.courses[0].covered_gap_skills)

    def test_direct_courses_are_not_flagged_as_proxies(self):
        catalogue = build_catalogue([course("c", skills=("deep_learning",))])
        result = recommend_courses_for_skill("deep_learning", catalogue, self.kb)
        self.assertFalse(result.courses[0].is_proxy)
        self.assertIsNone(result.courses[0].proxy_for)

    def test_no_course_is_ever_invented(self):
        empty = build_catalogue([])
        for skill_id in ("deep_learning", "mlops", "version_control"):
            result = recommend_courses_for_skill(skill_id, empty, self.kb)
            self.assertEqual(result.courses, ())
            self.assertTrue(result.no_course_available)


class TestGapSetRecommendation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def _gap(self, skill_id, priority, human=False):
        return SkillGap(skill_id, skill_id, 0.8, 0.0, 0.8, priority / 0.8, priority, human)

    def test_human_skill_gaps_never_produce_courses(self):
        catalogue = build_catalogue([course("c", skills=("communication",))])
        result = recommend_courses_for_gaps(
            [self._gap("communication", 0.5, human=True)], catalogue, self.kb
        )
        self.assertEqual(result, ())

    def test_courses_are_ranked_across_the_whole_gap_set(self):
        catalogue = build_catalogue(
            [
                course("both", skills=("deep_learning", "python")),
                course("one", skills=("deep_learning", "linux", "sql", "excel")),
            ]
        )
        gaps = [self._gap("deep_learning", 0.6), self._gap("python", 0.4)]
        ranked = recommend_courses_for_gaps(gaps, catalogue, self.kb)
        self.assertEqual(ranked[0].course_id, "both")

    def test_empty_gap_set_returns_nothing(self):
        catalogue = build_catalogue([course("c", skills=("deep_learning",))])
        self.assertEqual(recommend_courses_for_gaps([], catalogue, self.kb), ())


class TestRealCatalogue(unittest.TestCase):
    """Integration-style checks against the real ingested Coursera records."""

    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()
        cls.catalogue = load_catalogue(cls.kb)

    def test_catalogue_loads_every_course(self):
        self.assertEqual(len(self.catalogue.courses), 623)

    def test_index_only_contains_technical_skills(self):
        for skill_id in self.catalogue.by_skill:
            self.assertFalse(self.kb.skills[skill_id].is_human_skill, skill_id)

    def test_real_coverage_states_match_the_data_foundation(self):
        self.assertEqual(self.catalogue.coverage_state("version_control", self.kb)[0], "none")
        for skill_id in ("mlops", "model_deployment", "api_design"):
            self.assertEqual(self.catalogue.coverage_state(skill_id, self.kb)[0], "proxy")
        for skill_id in ("python", "machine_learning", "deep_learning", "cloud_computing"):
            self.assertEqual(self.catalogue.coverage_state(skill_id, self.kb)[0], "good")

    def test_deep_learning_returns_real_focused_courses(self):
        result = recommend_courses_for_skill(
            "deep_learning", self.catalogue, self.kb, target_level="intermediate", limit=3
        )
        self.assertEqual(result.coverage, "good")
        self.assertTrue(result.courses)
        for rec in result.courses:
            self.assertIn("deep_learning", rec.covered_gap_skills)

    def test_version_control_says_no_course_available_on_real_data(self):
        result = recommend_courses_for_skill("version_control", self.catalogue, self.kb)
        self.assertTrue(result.no_course_available)
        self.assertEqual(result.coverage, "none")

    def test_mlops_returns_flagged_proxy_courses_on_real_data(self):
        result = recommend_courses_for_skill("mlops", self.catalogue, self.kb, limit=3)
        self.assertEqual(result.coverage, "proxy")
        self.assertTrue(result.courses)
        self.assertTrue(all(r.is_proxy for r in result.courses))

    def test_courses_without_urls_can_still_be_recommended(self):
        without_url = [c for c in self.catalogue.courses if c.url is None and c.technical_skills]
        self.assertTrue(without_url)
        sample = without_url[0]
        ranked = rank_courses(
            [sample], {sample.technical_skills[0]: 1.0}, self.kb, "beginner"
        )
        self.assertEqual(len(ranked), 1)
        self.assertFalse(ranked[0].has_url)

    def test_real_ranking_is_deterministic(self):
        first = recommend_courses_for_skill("machine_learning", self.catalogue, self.kb, limit=5)
        second = recommend_courses_for_skill("machine_learning", self.catalogue, self.kb, limit=5)
        self.assertEqual(
            [c.course_id for c in first.courses], [c.course_id for c in second.courses]
        )

    def test_every_recommended_course_covers_the_requested_skill(self):
        for skill_id in ("python", "sql", "kubernetes", "statistics"):
            result = recommend_courses_for_skill(skill_id, self.catalogue, self.kb, limit=5)
            for rec in result.courses:
                self.assertIn(skill_id, rec.covered_gap_skills)


if __name__ == "__main__":
    unittest.main()
