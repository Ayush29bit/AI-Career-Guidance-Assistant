"""Deterministic learning roadmap.

A roadmap is an ordered sequence of skill gaps for one career, built from three
inputs that already exist: the prioritized technical gaps from the engine, the
prerequisite DAG in skills.yaml, and the course catalogue.

Two different orderings are at work and the output keeps them distinct:

  * gap priority       -- how much this skill matters (gap x importance)
  * prerequisite order -- what has to be learned first

Priority decides what to do next; prerequisites can override that by pushing a
high-priority skill later. Each stage reports its own priority rank alongside the
prerequisites that held it back, so the difference is visible rather than baked
invisibly into one number.

This is a deterministic sequence built from what we know. It is not an optimal
personalized curriculum and must not be presented as one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge.loader import Career, KnowledgeBase
from app.recommendation.courses import (
    CourseCatalogue,
    CourseRecommendation,
    recommend_courses_for_skill,
    target_difficulty_level,
)
from app.recommendation.engine import SkillGap, calculate_skill_gaps
from app.recommendation.profile import StudentProfile

#: Default number of stages. A 15-step roadmap is not a plan anyone follows.
#: Anything dropped is reported, never silently truncated.
DEFAULT_MAX_STAGES = 6

#: Courses suggested per stage.
COURSES_PER_STAGE = 2


@dataclass(frozen=True)
class RoadmapStage:
    stage_number: int
    skill_id: str
    skill_name: str
    gap: float
    required_level: float
    student_level: float
    priority: float
    priority_rank: int              # rank by gap priority alone, ignoring ordering
    blocked_by: tuple[str, ...]     # gap skills that had to come first
    coverage: str                   # good | thin | proxy | none
    courses: tuple[CourseRecommendation, ...]
    proxy_skills: tuple[str, ...] = ()

    @property
    def has_course(self) -> bool:
        return bool(self.courses)


@dataclass(frozen=True)
class Roadmap:
    career_id: str
    career_name: str
    target_level: str | None
    stages: tuple[RoadmapStage, ...]
    skipped_gaps: tuple[str, ...] = ()     # beyond max_stages; reported, not hidden
    uncovered_skills: tuple[str, ...] = ()  # stages the catalogue cannot serve

    @property
    def stage_count(self) -> int:
        return len(self.stages)


def select_roadmap_stages(
    gaps: tuple[SkillGap, ...] | list[SkillGap],
    kb: KnowledgeBase,
    max_stages: int | None = None,
) -> list[tuple[SkillGap, tuple[str, ...]]]:
    """Choose and order the gaps that make up the roadmap.

    Selection is driven by priority; ordering is driven by prerequisites. Working
    highest-priority-gap-first, each gap pulls in whichever of its prerequisites
    are themselves gaps (transitively) and places them before itself.

    Doing it this way round matters when the roadmap is capped. Ordering by
    prerequisites and then truncating would cut whatever sits deepest in the
    chain -- which is exactly where the highest-priority skills tend to live, so
    a six-stage roadmap could contain none of what the student most needs. Here,
    the top-priority gap and the things it depends on are chosen first, and it is
    the low-priority leaves that fall off the end.

    If a gap's prerequisite chain does not fit in the remaining stages, it is
    rolled back whole: half a chain that cannot reach its target is worse than
    spending those stages on the next gap down.

    Returns (gap, blocked_by) pairs, where blocked_by names the prerequisites
    that were placed before it, so the UI can distinguish "this is next because
    it matters most" from "this is next because it unlocks something else".

    Cycles cannot occur -- the knowledge base validator rejects them at load time
    -- but the recursion guards against one anyway.
    """
    by_id = {g.skill_id: g for g in gaps}
    limit = len(by_id) if max_stages is None else max_stages

    selected: list[SkillGap] = []
    selected_ids: set[str] = set()

    def add_with_prerequisites(skill_id: str, visiting: set[str]) -> None:
        if skill_id in selected_ids or skill_id not in by_id or skill_id in visiting:
            return
        visiting.add(skill_id)
        skill = kb.skills.get(skill_id)
        # sorted() keeps the order stable when a skill has several prerequisites
        for prerequisite in sorted(skill.prerequisites if skill else ()):
            add_with_prerequisites(prerequisite, visiting)
        visiting.discard(skill_id)
        selected.append(by_id[skill_id])
        selected_ids.add(skill_id)

    for gap in sorted(gaps, key=lambda g: (-g.priority, g.skill_id)):
        if len(selected) >= limit:
            break
        checkpoint = len(selected)
        add_with_prerequisites(gap.skill_id, set())
        if len(selected) > limit:
            del selected[checkpoint:]
            selected_ids = {g.skill_id for g in selected}

    placed_order = {gap.skill_id: index for index, gap in enumerate(selected)}
    result: list[tuple[SkillGap, tuple[str, ...]]] = []
    for index, gap in enumerate(selected):
        skill = kb.skills.get(gap.skill_id)
        blocked_by = tuple(
            sorted(
                p
                for p in (skill.prerequisites if skill else ())
                if p in placed_order and placed_order[p] < index
            )
        )
        result.append((gap, blocked_by))
    return result


def build_roadmap(
    profile: StudentProfile,
    career: Career,
    kb: KnowledgeBase,
    catalogue: CourseCatalogue,
    *,
    max_stages: int = DEFAULT_MAX_STAGES,
    courses_per_stage: int = COURSES_PER_STAGE,
) -> Roadmap:
    """Build the learning sequence from the student's gaps towards one career.

    Human-skill gaps are excluded: they are real, but they must not drive course
    recommendations, and a roadmap stage without a course purpose is noise.
    """
    technical_gaps, _human_gaps = calculate_skill_gaps(profile, career, kb)
    target_level = target_difficulty_level(profile, career.expected_experience)

    # Priority rank is computed before ordering, so the roadmap can show where
    # prerequisites overrode raw priority.
    by_priority = sorted(technical_gaps, key=lambda g: (-g.priority, g.skill_id))
    priority_rank = {g.skill_id: rank for rank, g in enumerate(by_priority, start=1)}

    ordered = select_roadmap_stages(technical_gaps, kb, max_stages)
    selected_ids = {gap.skill_id for gap, _ in ordered}

    stages: list[RoadmapStage] = []
    uncovered: list[str] = []
    used_course_ids: set[str] = set()

    for gap, blocked_by in ordered:
        result = recommend_courses_for_skill(
            gap.skill_id,
            catalogue,
            kb,
            target_level=target_level,
            priority=gap.priority,
            # over-fetch so courses already used earlier can be skipped
            limit=courses_per_stage + len(used_course_ids),
        )
        # A course already suggested at an earlier stage is not repeated.
        fresh = tuple(c for c in result.courses if c.course_id not in used_course_ids)[
            :courses_per_stage
        ]
        used_course_ids.update(c.course_id for c in fresh)

        if not fresh:
            uncovered.append(gap.skill_id)

        stages.append(
            RoadmapStage(
                stage_number=len(stages) + 1,
                skill_id=gap.skill_id,
                skill_name=gap.skill_name,
                gap=gap.gap,
                required_level=gap.required_level,
                student_level=gap.student_level,
                priority=gap.priority,
                priority_rank=priority_rank[gap.skill_id],
                blocked_by=blocked_by,
                coverage=result.coverage,
                courses=fresh,
                proxy_skills=result.proxy_skills,
            )
        )

    return Roadmap(
        career_id=career.id,
        career_name=career.name,
        target_level=target_level,
        stages=tuple(stages),
        skipped_gaps=tuple(
            g.skill_id
            for g in sorted(technical_gaps, key=lambda g: (-g.priority, g.skill_id))
            if g.skill_id not in selected_ids
        ),
        uncovered_skills=tuple(uncovered),
    )
