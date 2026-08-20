"""Tests for deterministic roadmap generation."""

from __future__ import annotations

import unittest

from app.ingestion.coursera import NormalizedCourse
from app.knowledge.loader import (
    Career,
    KnowledgeBase,
    RequiredSkill,
    Skill,
    load_knowledge_base,
)
from app.recommendation.courses import build_catalogue, load_catalogue
from app.recommendation.engine import SkillGap
from app.recommendation.profile import StudentProfile, build_profile
from app.recommendation.roadmap import build_roadmap, select_roadmap_stages


def course(course_id: str, skills: tuple[str, ...], rating: float = 4.7) -> NormalizedCourse:
    return NormalizedCourse(
        course_id=course_id,
        title=course_id.replace("-", " ").title(),
        organization="Test Org",
        url="https://example.test/c",
        rating=rating,
        review_count=1000,
        review_count_raw="1000",
        review_count_is_approximate=False,
        students_enrolled=None,
        difficulty="beginner",
        course_type="course",
        duration="1_3_months",
        raw_skills=(),
        skills=skills,
        technical_skills=skills,
        dropped_skills=(),
    )


def chain_kb() -> KnowledgeBase:
    """basics -> middle -> advanced, plus one standalone skill and one human skill."""
    def skill(sid, name, prereqs=(), kind="concept", related=()):
        return Skill(sid, name, "programming", kind, (), tuple(prereqs), tuple(related))

    skills = {
        "basics": skill("basics", "Basics"),
        "middle": skill("middle", "Middle", ["basics"]),
        "advanced": skill("advanced", "Advanced", ["middle"]),
        "standalone": skill("standalone", "Standalone"),
        "uncovered": skill("uncovered", "Uncovered"),
        "teamwork": skill("teamwork", "Teamwork", kind="human"),
    }
    careers = {
        "target": Career(
            id="target",
            name="Target Career",
            short_description="Synthetic target.",
            expected_experience="beginner",
            interest_tags=("software",),
            work_tags=("building",),
            required_skills=(
                # advanced has the highest priority but must come last
                RequiredSkill("advanced", importance=1.0, required_level=0.9),
                RequiredSkill("middle", importance=0.5, required_level=0.6),
                RequiredSkill("basics", importance=0.4, required_level=0.5),
                RequiredSkill("standalone", importance=0.45, required_level=0.6),
                RequiredSkill("uncovered", importance=0.3, required_level=0.5),
                RequiredSkill("teamwork", importance=0.9, required_level=0.9),
            ),
        )
    }
    return KnowledgeBase(
        skills=skills,
        careers=careers,
        out_of_scope_skills=frozenset(),
        categories=("programming",),
        kinds=("concept", "human"),
        interest_tags=("software",),
        work_tags=("building",),
        experience_levels=("beginner", "intermediate", "advanced"),
        alias_map={},
    )


def chain_catalogue():
    return build_catalogue(
        [
            course("basics-course", ("basics",)),
            course("middle-course", ("middle",)),
            course("advanced-course", ("advanced",)),
            course("standalone-course", ("standalone",)),
            # nothing for "uncovered"
        ]
    )


class TestStageSelectionAndOrdering(unittest.TestCase):
    def setUp(self):
        self.kb = chain_kb()

    def _gap(self, skill_id: str, priority: float) -> SkillGap:
        return SkillGap(skill_id, skill_id, 0.8, 0.0, 0.8, priority / 0.8, priority, False)

    def test_prerequisites_come_first_even_at_lower_priority(self):
        gaps = [self._gap("advanced", 0.9), self._gap("basics", 0.2), self._gap("middle", 0.3)]
        ordered = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb)]
        self.assertEqual(ordered, ["basics", "middle", "advanced"])

    def test_priority_decides_among_unblocked_gaps(self):
        gaps = [self._gap("standalone", 0.2), self._gap("basics", 0.9)]
        ordered = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb)]
        self.assertEqual(ordered, ["basics", "standalone"])

    def test_satisfied_prerequisites_do_not_block(self):
        """The student already has 'basics', so 'middle' is free to go first."""
        gaps = [self._gap("middle", 0.9), self._gap("standalone", 0.5)]
        ordered = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb)]
        self.assertEqual(ordered, ["middle", "standalone"])

    def test_blocked_by_records_the_prerequisite(self):
        gaps = [self._gap("advanced", 0.9), self._gap("middle", 0.3), self._gap("basics", 0.2)]
        ordered = dict((g.skill_id, blocked) for g, blocked in select_roadmap_stages(gaps, self.kb))
        self.assertEqual(ordered["basics"], ())
        self.assertEqual(ordered["middle"], ("basics",))
        self.assertEqual(ordered["advanced"], ("middle",))

    def test_ordering_is_deterministic(self):
        gaps = [self._gap("standalone", 0.5), self._gap("basics", 0.5)]
        first = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb)]
        second = [g.skill_id for g, _ in select_roadmap_stages(list(reversed(gaps)), self.kb)]
        self.assertEqual(first, second)

    def test_empty_gaps_produce_an_empty_order(self):
        self.assertEqual(select_roadmap_stages([], self.kb), [])

    def test_capped_selection_keeps_the_top_priority_gap_and_its_chain(self):
        """Selection is priority-driven, so a cap must not cut what matters most.

        Ordering by prerequisites and then truncating would keep basics/middle
        and drop 'advanced' -- the highest-priority gap -- which is the opposite
        of what a learning plan should do.
        """
        gaps = [
            self._gap("advanced", 0.9),
            self._gap("middle", 0.3),
            self._gap("basics", 0.2),
            self._gap("standalone", 0.25),
        ]
        selected = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb, max_stages=3)]
        self.assertEqual(selected, ["basics", "middle", "advanced"])
        self.assertNotIn("standalone", selected)

    def test_a_chain_that_does_not_fit_is_rolled_back_whole(self):
        """Half a prerequisite chain cannot reach its target, so it is not started."""
        gaps = [
            self._gap("advanced", 0.9),   # closure is basics + middle + advanced = 3 stages
            self._gap("middle", 0.3),     # closure is basics + middle = 2 stages
            self._gap("basics", 0.2),
        ]
        selected = [g.skill_id for g, _ in select_roadmap_stages(gaps, self.kb, max_stages=2)]
        # advanced's chain does not fit, so the next-best whole chain is taken
        self.assertEqual(selected, ["basics", "middle"])
        self.assertNotIn("advanced", selected)

    def test_cap_is_never_exceeded(self):
        gaps = [
            self._gap("advanced", 0.9), self._gap("middle", 0.3),
            self._gap("basics", 0.2), self._gap("standalone", 0.25),
            self._gap("uncovered", 0.1),
        ]
        for limit in range(1, 6):
            selected = select_roadmap_stages(gaps, self.kb, max_stages=limit)
            self.assertLessEqual(len(selected), limit)


class TestRoadmapConstruction(unittest.TestCase):
    def setUp(self):
        self.kb = chain_kb()
        self.career = self.kb.careers["target"]
        self.catalogue = chain_catalogue()
        self.profile = StudentProfile(
            skills={}, interests=("software",), work_preferences=("building",),
            experience_level="beginner",
        )

    def build(self, **kwargs):
        return build_roadmap(self.profile, self.career, self.kb, self.catalogue, **kwargs)

    def test_stages_respect_prerequisite_order(self):
        roadmap = self.build()
        order = [s.skill_id for s in roadmap.stages]
        self.assertLess(order.index("basics"), order.index("middle"))
        self.assertLess(order.index("middle"), order.index("advanced"))

    def test_priority_rank_is_reported_separately_from_stage_order(self):
        """'advanced' is the top priority but must not be the first stage."""
        roadmap = self.build()
        advanced = next(s for s in roadmap.stages if s.skill_id == "advanced")
        self.assertEqual(advanced.priority_rank, 1)
        self.assertGreater(advanced.stage_number, 1)
        self.assertEqual(advanced.blocked_by, ("middle",))

    def test_stage_numbers_are_sequential(self):
        roadmap = self.build()
        self.assertEqual(
            [s.stage_number for s in roadmap.stages], list(range(1, len(roadmap.stages) + 1))
        )

    def test_no_duplicate_skills(self):
        roadmap = self.build()
        ids = [s.skill_id for s in roadmap.stages]
        self.assertEqual(len(ids), len(set(ids)))

    def test_human_skills_never_become_stages(self):
        roadmap = self.build()
        self.assertNotIn("teamwork", [s.skill_id for s in roadmap.stages])

    def test_courses_are_attached_where_available(self):
        roadmap = self.build()
        basics = next(s for s in roadmap.stages if s.skill_id == "basics")
        self.assertTrue(basics.has_course)
        self.assertEqual(basics.courses[0].course_id, "basics-course")

    def test_stage_without_a_course_still_exists(self):
        roadmap = self.build()
        uncovered = next(s for s in roadmap.stages if s.skill_id == "uncovered")
        self.assertFalse(uncovered.has_course)
        self.assertEqual(uncovered.coverage, "none")
        self.assertIn("uncovered", roadmap.uncovered_skills)

    def test_a_course_is_not_repeated_across_stages(self):
        catalogue = build_catalogue(
            [course("everything", ("basics", "middle", "advanced", "standalone"))]
        )
        roadmap = build_roadmap(self.profile, self.career, self.kb, catalogue)
        used = [c.course_id for s in roadmap.stages for c in s.courses]
        self.assertEqual(len(used), len(set(used)))

    def test_max_stages_is_respected_and_the_rest_reported(self):
        roadmap = self.build(max_stages=2)
        self.assertEqual(len(roadmap.stages), 2)
        self.assertTrue(roadmap.skipped_gaps)
        total = len(roadmap.stages) + len(roadmap.skipped_gaps)
        self.assertEqual(total, 5)  # five technical gaps, teamwork excluded

    def test_nothing_is_truncated_silently(self):
        """Every technical gap is either a stage or explicitly reported as skipped."""
        all_gaps = {"basics", "middle", "advanced", "standalone", "uncovered"}
        for limit in (1, 2, 3, 5):
            roadmap = self.build(max_stages=limit)
            accounted = {s.skill_id for s in roadmap.stages} | set(roadmap.skipped_gaps)
            self.assertEqual(accounted, all_gaps, f"max_stages={limit}")
            self.assertEqual(len(roadmap.stages), min(limit, len(all_gaps)))

    def test_roadmap_is_deterministic(self):
        first = self.build()
        second = self.build()
        self.assertEqual(
            [(s.stage_number, s.skill_id, tuple(c.course_id for c in s.courses)) for s in first.stages],
            [(s.stage_number, s.skill_id, tuple(c.course_id for c in s.courses)) for s in second.stages],
        )

    def test_no_gaps_produces_an_empty_roadmap(self):
        complete = StudentProfile(
            skills={"basics": 1.0, "middle": 1.0, "advanced": 1.0, "standalone": 1.0,
                    "uncovered": 1.0, "teamwork": 1.0},
            interests=("software",),
            work_preferences=("building",),
            experience_level="beginner",
        )
        roadmap = build_roadmap(complete, self.career, self.kb, self.catalogue)
        self.assertEqual(roadmap.stages, ())
        self.assertEqual(roadmap.skipped_gaps, ())

    def test_target_level_falls_back_to_the_career(self):
        no_experience = StudentProfile(
            interests=("software",), work_preferences=("building",), experience_level=None
        )
        roadmap = build_roadmap(no_experience, self.career, self.kb, self.catalogue)
        self.assertEqual(roadmap.target_level, "beginner")


class TestRoadmapOnRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()
        cls.catalogue = load_catalogue(cls.kb)

    def ml_student(self):
        return build_profile(
            self.kb,
            skills={"python": 0.85, "machine_learning": 0.70, "deep_learning": 0.45,
                    "statistics": 0.55, "sql": 0.45, "mathematics": 0.60},
            interests=["ai_ml", "data", "software"],
            work_preferences=["building", "experimentation", "problem_solving"],
            experience_level="intermediate",
        )

    def test_real_roadmap_builds(self):
        roadmap = build_roadmap(
            self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue
        )
        self.assertTrue(roadmap.stages)
        self.assertEqual(roadmap.career_id, "ml_engineer")

    def test_prerequisites_hold_on_real_data(self):
        roadmap = build_roadmap(
            self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue,
            max_stages=20,
        )
        order = [s.skill_id for s in roadmap.stages]
        for stage in roadmap.stages:
            for prerequisite in self.kb.skills[stage.skill_id].prerequisites:
                if prerequisite in order:
                    self.assertLess(order.index(prerequisite), order.index(stage.skill_id))

    def test_version_control_stage_has_no_course_on_real_data(self):
        roadmap = build_roadmap(
            self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue,
            max_stages=20,
        )
        stage = next((s for s in roadmap.stages if s.skill_id == "version_control"), None)
        self.assertIsNotNone(stage)
        self.assertEqual(stage.coverage, "none")
        self.assertFalse(stage.has_course)

    def test_mlops_stage_is_marked_proxy_on_real_data(self):
        roadmap = build_roadmap(
            self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue,
            max_stages=20,
        )
        stage = next((s for s in roadmap.stages if s.skill_id == "mlops"), None)
        self.assertIsNotNone(stage)
        self.assertEqual(stage.coverage, "proxy")
        if stage.courses:
            self.assertTrue(all(c.is_proxy for c in stage.courses))

    def test_no_duplicate_courses_across_a_real_roadmap(self):
        roadmap = build_roadmap(
            self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue,
            max_stages=20,
        )
        used = [c.course_id for s in roadmap.stages for c in s.courses]
        self.assertEqual(len(used), len(set(used)))

    def test_real_roadmap_is_deterministic(self):
        args = (self.ml_student(), self.kb.careers["ml_engineer"], self.kb, self.catalogue)
        first = build_roadmap(*args)
        second = build_roadmap(*args)
        self.assertEqual(
            [(s.skill_id, tuple(c.course_id for c in s.courses)) for s in first.stages],
            [(s.skill_id, tuple(c.course_id for c in s.courses)) for s in second.stages],
        )

    def test_roadmaps_build_for_every_career(self):
        profile = self.ml_student()
        for career in self.kb.careers.values():
            roadmap = build_roadmap(profile, career, self.kb, self.catalogue)
            self.assertEqual(roadmap.career_id, career.id)
            ids = [s.skill_id for s in roadmap.stages]
            self.assertEqual(len(ids), len(set(ids)), career.id)


if __name__ == "__main__":
    unittest.main()
