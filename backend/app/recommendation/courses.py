"""Deterministic course retrieval and ranking.

Courses are matched only on the canonical skill ids produced by the ingestion
step. Raw Coursera skill strings are never touched at runtime -- normalization
happened once, deterministically, and this module consumes the result.

Ranking formula, exactly as specified in docs/RECOMMENDATIONS.md:

    Course Score = 50% skill coverage
                 + 20% difficulty fit
                 + 20% rating
                 + 10% popularity

The 50% skill-coverage component is the approved blend of two measures:

    skill_coverage = 0.60 * gap_coverage + 0.40 * course_focus

    gap_coverage = (summed priority of the requested gaps this course covers)
                 / (summed priority of all requested gaps)
    course_focus = (number of requested gaps this course covers)
                 / (number of technical skills the course teaches)

gap_coverage alone rewards breadth, so a 40-skill certificate would win every
query. course_focus asks the opposite question -- how much of this course is
about what you need right now -- and a three-skill TensorFlow course beats the
catch-all on it. Blending the two is what stops mega-certificates dominating.

Zero relevance is a hard gate, not a low score: a course covering none of the
requested gaps scores 0 overall, so a high rating or a large review count can
never turn an irrelevant course into a recommendation.

course_description is never read here. The dataset audit found rows carrying
another course's text, so it is display-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.coursera import NormalizedCourse, ingest
from app.knowledge.loader import KnowledgeBase
from app.recommendation.engine import SkillGap
from app.recommendation.profile import StudentProfile

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET = REPO_ROOT / "data" / "coursera_course_dataset_v3.csv"

# --------------------------------------------------------------------------
# tunable constants
# --------------------------------------------------------------------------

#: Top-level weights. Fixed by docs/RECOMMENDATIONS.md; do not retune casually.
WEIGHT_COVERAGE = 0.50
WEIGHT_DIFFICULTY = 0.20
WEIGHT_RATING = 0.20
WEIGHT_POPULARITY = 0.10

#: Internal blend inside the 50% coverage term.
BLEND_GAP_COVERAGE = 0.60
BLEND_COURSE_FOCUS = 0.40

#: Rating is normalized on a fixed absolute scale rather than stretched across
#: the observed range. 95% of this catalogue sits between 4.3 and 4.9, so
#: min-max scaling would turn rounding noise into large score differences.
RATING_FLOOR = 3.0
RATING_CEILING = 5.0

#: Popularity uses a log scale against a fixed reference, so a course's score
#: does not change depending on which other courses happen to be candidates.
POPULARITY_REFERENCE = 200_000

#: Neutral values for fields the dataset may not supply.
NEUTRAL_RATING = 0.5
NEUTRAL_DIFFICULTY_FIT = 0.5

#: A skill is well covered at or above this many courses.
GOOD_COVERAGE_MIN = 10

_LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}

#: Difficulty fit by (course level - target level). Asymmetric on purpose: a
#: course that is too advanced blocks the student outright, while one that is
#: too easy merely wastes some time.
_DIFFICULTY_FIT_BY_DISTANCE = {
    0: 1.0,    # exactly the right level
    1: 0.6,    # one level harder than the student is ready for
    2: 0.25,   # two levels harder
    -1: 0.7,   # one level easier than needed
    -2: 0.4,   # two levels easier
}

#: "Mixed" spans several levels, so it is broadly usable but never as well
#: targeted as an exact match. One constant, applied to every target level.
MIXED_DIFFICULTY_FIT = 0.8


# --------------------------------------------------------------------------
# result types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CourseScoreBreakdown:
    skill_coverage: float
    gap_coverage: float
    course_focus: float
    difficulty_fit: float
    rating_score: float
    popularity_score: float
    coverage_contribution: float
    difficulty_contribution: float
    rating_contribution: float
    popularity_contribution: float


@dataclass(frozen=True)
class CourseRecommendation:
    course_id: str
    title: str
    organization: str
    url: str | None
    has_url: bool
    score: float
    score_100: int
    breakdown: CourseScoreBreakdown
    covered_gap_skills: tuple[str, ...]
    covered_gap_skill_names: tuple[str, ...]
    is_proxy: bool
    proxy_for: str | None
    difficulty: str | None
    course_type: str | None
    duration: str | None
    rating: float | None
    review_count: int | None
    review_count_is_approximate: bool
    total_technical_skills: int


@dataclass(frozen=True)
class SkillCourseResult:
    """Courses for one skill gap, or an explicit statement that there are none."""

    skill_id: str
    skill_name: str
    coverage: str  # good | thin | proxy | none
    courses: tuple[CourseRecommendation, ...]
    proxy_skills: tuple[str, ...] = ()
    direct_course_count: int = 0

    @property
    def no_course_available(self) -> bool:
        return not self.courses


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------

@dataclass
class CourseCatalogue:
    """Normalized courses indexed by canonical skill id."""

    courses: tuple[NormalizedCourse, ...]
    by_skill: dict[str, tuple[NormalizedCourse, ...]]

    def for_skill(self, skill_id: str) -> tuple[NormalizedCourse, ...]:
        return self.by_skill.get(skill_id, ())

    def course_count(self, skill_id: str) -> int:
        return len(self.by_skill.get(skill_id, ()))

    def coverage_state(self, skill_id: str, kb: KnowledgeBase) -> tuple[str, tuple[str, ...]]:
        """Coverage for one skill, using the states agreed in the data foundation.

        Returns (state, proxy_skills). The proxy list is only populated when the
        state is "proxy".
        """
        count = self.course_count(skill_id)
        if count >= GOOD_COVERAGE_MIN:
            return "good", ()
        if count > 0:
            return "thin", ()
        skill = kb.skills.get(skill_id)
        if skill is not None:
            proxies = tuple(r for r in skill.related if self.course_count(r) > 0)
            if proxies:
                return "proxy", proxies
        return "none", ()


def build_catalogue(courses: list[NormalizedCourse] | tuple[NormalizedCourse, ...]) -> CourseCatalogue:
    """Index courses by the canonical technical skills they teach.

    Human skills are excluded from the index by construction: the ingestion step
    already separates them out, and they must never drive course recommendations.
    """
    index: dict[str, list[NormalizedCourse]] = {}
    for course in courses:
        for skill_id in course.technical_skills:
            index.setdefault(skill_id, []).append(course)
    return CourseCatalogue(
        courses=tuple(courses),
        # course_id sort keeps every downstream ranking stable
        by_skill={k: tuple(sorted(v, key=lambda c: c.course_id)) for k, v in index.items()},
    )


def load_catalogue(kb: KnowledgeBase, dataset: Path = DATASET) -> CourseCatalogue:
    """Load the catalogue by running the existing deterministic ingestion.

    Reads the raw CSV through the ingestion module rather than the generated JSON
    so there is only ever one normalization path. The CSV is opened read-only.
    """
    return build_catalogue(ingest(dataset, kb).courses)


# --------------------------------------------------------------------------
# component scores
# --------------------------------------------------------------------------

def rating_score(rating: float | None) -> float:
    """Map a Coursera rating onto 0..1 using a fixed absolute scale."""
    if rating is None:
        return NEUTRAL_RATING
    span = RATING_CEILING - RATING_FLOOR
    return max(0.0, min(1.0, (rating - RATING_FLOOR) / span))


def popularity_score(review_count: int | None) -> float:
    """Log-scaled review count against a fixed reference.

    Review Count is used because it is present for all 623 courses, while
    enrolment is missing on 38%. The distribution runs from 6 to ~3.6M, so a
    linear scale would make everything except a handful of megacourses identical.
    """
    if not review_count or review_count <= 0:
        return 0.0
    scaled = math.log10(1 + review_count) / math.log10(1 + POPULARITY_REFERENCE)
    return max(0.0, min(1.0, scaled))


def difficulty_fit_score(course_difficulty: str | None, target_level: str | None) -> float:
    """How well a course's difficulty suits the level we are aiming at."""
    if course_difficulty == "mixed":
        return MIXED_DIFFICULTY_FIT
    if course_difficulty not in _LEVEL_ORDER or target_level not in _LEVEL_ORDER:
        return NEUTRAL_DIFFICULTY_FIT
    distance = _LEVEL_ORDER[course_difficulty] - _LEVEL_ORDER[target_level]
    return _DIFFICULTY_FIT_BY_DISTANCE.get(distance, NEUTRAL_DIFFICULTY_FIT)


def target_difficulty_level(
    profile: StudentProfile | None, career_expected_experience: str | None
) -> str | None:
    """The level courses should be pitched at.

    The student's own experience wins when known, because that is what decides
    whether they can follow the material. The career's expected level is the
    fallback so a cold-ish profile still gets sensibly pitched courses.
    """
    if profile is not None and profile.experience_level:
        return profile.experience_level
    return career_expected_experience


def coverage_scores(
    course: NormalizedCourse, gap_priorities: dict[str, float]
) -> tuple[float, float, float, tuple[str, ...]]:
    """Return (skill_coverage, gap_coverage, course_focus, covered_skill_ids)."""
    covered = tuple(s for s in course.technical_skills if s in gap_priorities)
    if not covered:
        return 0.0, 0.0, 0.0, ()

    total_priority = sum(gap_priorities.values())
    gap_coverage = (
        sum(gap_priorities[s] for s in covered) / total_priority if total_priority > 0 else 0.0
    )
    course_focus = len(covered) / len(course.technical_skills)
    blended = BLEND_GAP_COVERAGE * gap_coverage + BLEND_COURSE_FOCUS * course_focus
    return blended, gap_coverage, course_focus, covered


# --------------------------------------------------------------------------
# scoring one course
# --------------------------------------------------------------------------

def score_course(
    course: NormalizedCourse,
    gap_priorities: dict[str, float],
    kb: KnowledgeBase,
    target_level: str | None,
    *,
    is_proxy: bool = False,
    proxy_for: str | None = None,
) -> CourseRecommendation:
    """Score one course against a set of prioritized gaps.

    A course covering none of the gaps scores exactly 0 -- rating and popularity
    cannot compensate for zero relevance.
    """
    skill_coverage, gap_coverage, course_focus, covered = coverage_scores(course, gap_priorities)
    difficulty = difficulty_fit_score(course.difficulty, target_level)
    rating = rating_score(course.rating)
    popularity = popularity_score(course.review_count)

    if skill_coverage <= 0.0:
        score = 0.0
        contributions = (0.0, 0.0, 0.0, 0.0)
    else:
        contributions = (
            WEIGHT_COVERAGE * skill_coverage,
            WEIGHT_DIFFICULTY * difficulty,
            WEIGHT_RATING * rating,
            WEIGHT_POPULARITY * popularity,
        )
        score = sum(contributions)

    return CourseRecommendation(
        course_id=course.course_id,
        title=course.title,
        organization=course.organization,
        url=course.url,
        has_url=course.url is not None,
        score=score,
        score_100=round(score * 100),
        breakdown=CourseScoreBreakdown(
            skill_coverage=skill_coverage,
            gap_coverage=gap_coverage,
            course_focus=course_focus,
            difficulty_fit=difficulty,
            rating_score=rating,
            popularity_score=popularity,
            coverage_contribution=contributions[0],
            difficulty_contribution=contributions[1],
            rating_contribution=contributions[2],
            popularity_contribution=contributions[3],
        ),
        covered_gap_skills=covered,
        covered_gap_skill_names=tuple(
            kb.skills[s].name if s in kb.skills else s for s in covered
        ),
        is_proxy=is_proxy,
        proxy_for=proxy_for,
        difficulty=course.difficulty,
        course_type=course.course_type,
        duration=course.duration,
        rating=course.rating,
        review_count=course.review_count,
        review_count_is_approximate=course.review_count_is_approximate,
        total_technical_skills=len(course.technical_skills),
    )


def rank_courses(
    candidates: list[NormalizedCourse] | tuple[NormalizedCourse, ...],
    gap_priorities: dict[str, float],
    kb: KnowledgeBase,
    target_level: str | None,
    *,
    limit: int | None = 5,
    is_proxy: bool = False,
    proxy_for: str | None = None,
) -> tuple[CourseRecommendation, ...]:
    """Score and rank candidates, dropping anything with zero relevance."""
    scored = [
        score_course(c, gap_priorities, kb, target_level, is_proxy=is_proxy, proxy_for=proxy_for)
        for c in candidates
    ]
    relevant = [r for r in scored if r.score > 0.0]
    # course_id breaks ties so equal-scoring courses never swap between runs
    relevant.sort(key=lambda r: (-r.score, r.course_id))
    return tuple(relevant[:limit] if limit is not None else relevant)


# --------------------------------------------------------------------------
# per-skill and per-gap-set recommendation
# --------------------------------------------------------------------------

def recommend_courses_for_skill(
    skill_id: str,
    catalogue: CourseCatalogue,
    kb: KnowledgeBase,
    *,
    target_level: str | None = None,
    priority: float = 1.0,
    limit: int | None = 3,
) -> SkillCourseResult:
    """Best courses for one skill, or an honest statement that there are none.

    When the skill itself has no courses but declares adjacent `related` skills
    that do, those courses are returned flagged as proxies, so the UI and the LLM
    can say plainly that the course covers an adjacent concept rather than the
    missing skill itself.
    """
    skill = kb.skills.get(skill_id)
    skill_name = skill.name if skill else skill_id
    coverage, proxy_skills = catalogue.coverage_state(skill_id, kb)

    if coverage in ("good", "thin"):
        courses = rank_courses(
            catalogue.for_skill(skill_id), {skill_id: priority}, kb, target_level, limit=limit
        )
        return SkillCourseResult(
            skill_id=skill_id,
            skill_name=skill_name,
            coverage=coverage,
            courses=courses,
            direct_course_count=catalogue.course_count(skill_id),
        )

    if coverage == "proxy":
        proxy_priorities = {p: priority for p in proxy_skills}
        candidates = {
            c.course_id: c for p in proxy_skills for c in catalogue.for_skill(p)
        }
        courses = rank_courses(
            sorted(candidates.values(), key=lambda c: c.course_id),
            proxy_priorities,
            kb,
            target_level,
            limit=limit,
            is_proxy=True,
            proxy_for=skill_id,
        )
        return SkillCourseResult(
            skill_id=skill_id,
            skill_name=skill_name,
            coverage="proxy",
            courses=courses,
            proxy_skills=proxy_skills,
            direct_course_count=0,
        )

    # Nothing at all. Say so; never invent a course.
    return SkillCourseResult(
        skill_id=skill_id,
        skill_name=skill_name,
        coverage="none",
        courses=(),
        direct_course_count=0,
    )


def recommend_courses_for_gaps(
    gaps: tuple[SkillGap, ...] | list[SkillGap],
    catalogue: CourseCatalogue,
    kb: KnowledgeBase,
    *,
    target_level: str | None = None,
    limit: int | None = 5,
) -> tuple[CourseRecommendation, ...]:
    """Courses ranked against a whole prioritized gap set at once.

    Used when the question is "what should I take to close these gaps", as
    opposed to the per-skill view used by the roadmap.
    """
    gap_priorities = {g.skill_id: g.priority for g in gaps if not g.is_human_skill}
    if not gap_priorities:
        return ()
    candidates = {
        c.course_id: c for skill_id in gap_priorities for c in catalogue.for_skill(skill_id)
    }
    return rank_courses(
        sorted(candidates.values(), key=lambda c: c.course_id),
        gap_priorities,
        kb,
        target_level,
        limit=limit,
    )
