"""Deterministic career matching and skill-gap logic.

This module is the "decisions and calculations" half of the system. It contains
no LLM, no embeddings, no machine learning and no fuzzy matching: every number
below is reproducible from the student profile plus the YAML knowledge base, and
every one can be traced back to an input by hand.

The LLM never computes any part of a score. It receives the structured output of
this module and turns it into natural language.

Scoring formula, exactly as specified in docs/RECOMMENDATIONS.md:

    Career Score = 50% skill match
                 + 25% interest match
                 + 15% preference match
                 + 10% experience fit
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.knowledge.loader import Career, KnowledgeBase, RequiredSkill
from app.recommendation.profile import StudentProfile, validate_profile
from app.recommendation.profile import InvalidProfileError

# --------------------------------------------------------------------------
# tunable constants -- all in one place, none buried in the logic below
# --------------------------------------------------------------------------

WEIGHT_SKILL = 0.50
WEIGHT_INTEREST = 0.25
WEIGHT_PREFERENCE = 0.15
WEIGHT_EXPERIENCE = 0.10

#: Minimum information before the engine is willing to rank careers at all.
#: Below this the honest answer is "I don't know enough yet" (RECOMMENDATIONS.md
#: "Cold Start"), not a confident-looking ranking built from nothing.
MIN_SKILLS = 3
MIN_INTERESTS = 1
MIN_WORK_PREFERENCES = 1

#: A required skill counts as a strength at or above this attainment, and as a
#: concern below the second threshold.
STRENGTH_ATTAINMENT = 0.75
CONCERN_ATTAINMENT = 0.50

#: Experience is unknown often enough that it needs a defined neutral value
#: rather than a guess. 0.5 means "neither credit nor penalty".
UNKNOWN_EXPERIENCE_FIT = 0.5

#: Float tolerance, so a gap of 1e-17 caused by binary rounding is not reported.
EPSILON = 1e-9

_EXPERIENCE_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}

#: Experience compatibility by (student level - career expected level).
#: Meeting or exceeding what a career expects is a full fit -- being
#: overqualified is not a penalty. Falling short costs progressively.
_EXPERIENCE_FIT_BY_DISTANCE = {
    2: 1.0,   # advanced student, beginner-level career
    1: 1.0,   # one level above what the career expects
    0: 1.0,   # exactly the expected level
    -1: 0.6,  # one level short
    -2: 0.3,  # two levels short
}


# --------------------------------------------------------------------------
# result types -- facts and numbers only, no prose
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchFactor:
    """One concrete reason a career does or does not fit, as data.

    `kind` is skill | interest | work_preference | experience.
    `value` is 0..1: skill attainment, 1.0 for a matched tag, or experience fit.
    """

    kind: str
    id: str
    label: str
    value: float
    weight: float = 0.0


@dataclass(frozen=True)
class SkillGap:
    skill_id: str
    skill_name: str
    required_level: float
    student_level: float
    gap: float
    importance: float
    priority: float
    is_human_skill: bool


@dataclass(frozen=True)
class ScoreBreakdown:
    """Each component in 0..1 plus what it contributed to the final score."""

    skill_match: float
    interest_match: float
    preference_match: float
    experience_fit: float
    skill_contribution: float
    interest_contribution: float
    preference_contribution: float
    experience_contribution: float
    experience_known: bool


@dataclass(frozen=True)
class CareerMatch:
    career_id: str
    career_name: str
    short_description: str
    score: float           # 0..1, full precision; ranking uses this
    score_100: int         # rounded for display
    breakdown: ScoreBreakdown
    strengths: tuple[MatchFactor, ...]
    concerns: tuple[MatchFactor, ...]
    matching_interests: tuple[str, ...]
    missing_interests: tuple[str, ...]
    matching_preferences: tuple[str, ...]
    missing_preferences: tuple[str, ...]
    skill_gaps: tuple[SkillGap, ...]        # technical only; drives courses later
    human_skill_gaps: tuple[SkillGap, ...]  # never drives course recommendations


@dataclass(frozen=True)
class MissingInformation:
    field_name: str
    have: int
    need: int


@dataclass
class RecommendationResult:
    """Either a ranking, or an honest statement that we cannot rank yet."""

    status: str  # "ok" | "insufficient_profile"
    matches: tuple[CareerMatch, ...] = ()
    missing_information: tuple[MissingInformation, ...] = field(default_factory=tuple)
    considered_careers: int = 0

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


# --------------------------------------------------------------------------
# component scores
# --------------------------------------------------------------------------

def skill_attainment(student_level: float, requirement: RequiredSkill) -> float:
    """How much of one skill requirement the student meets, in 0..1.

    Proportional up to the level the career actually needs, then capped: being
    stronger than a career requires does not earn extra credit, because the
    surplus cannot make up for a different missing skill.
    """
    if requirement.required_level <= 0:
        return 1.0
    return min(1.0, student_level / requirement.required_level)


def skill_match_score(profile: StudentProfile, career: Career) -> float:
    """Importance-weighted mean attainment across the career's required skills.

    Missing skills contribute 0 rather than being skipped, so an empty profile
    scores 0 instead of scoring well on the one skill it happens to have.
    """
    total_importance = sum(r.importance for r in career.required_skills)
    if total_importance <= 0:
        return 0.0
    weighted = sum(
        r.importance * skill_attainment(profile.proficiency(r.skill), r)
        for r in career.required_skills
    )
    return weighted / total_importance


def tag_match_score(student_tags: tuple[str, ...], career_tags: tuple[str, ...]) -> float:
    """Exact set overlap as a fraction of what the career calls for.

    Deliberately literal: tags come from closed vocabularies, so there is nothing
    to disambiguate and no reason for fuzzy or semantic matching.
    """
    if not career_tags:
        return 0.0
    matched = len(set(student_tags) & set(career_tags))
    return matched / len(career_tags)


def experience_fit_score(student_level: str | None, career_level: str) -> tuple[float, bool]:
    """Compatibility between the student's experience and what the career expects.

    Returns (fit, known). Unknown experience is neutral, never guessed.
    """
    if student_level is None:
        return UNKNOWN_EXPERIENCE_FIT, False
    if student_level not in _EXPERIENCE_ORDER or career_level not in _EXPERIENCE_ORDER:
        return UNKNOWN_EXPERIENCE_FIT, False
    distance = _EXPERIENCE_ORDER[student_level] - _EXPERIENCE_ORDER[career_level]
    return _EXPERIENCE_FIT_BY_DISTANCE[distance], True


# --------------------------------------------------------------------------
# skill gaps
# --------------------------------------------------------------------------

def calculate_skill_gaps(
    profile: StudentProfile, career: Career, kb: KnowledgeBase
) -> tuple[tuple[SkillGap, ...], tuple[SkillGap, ...]]:
    """Gaps for one career, ranked by priority.

        gap      = required_level - student_level   (positive gaps only)
        priority = gap * importance

    Ranking by gap alone would put a large gap in a marginal skill above a
    moderate gap in a critical one, so importance is part of the ordering.

    Returns (technical_gaps, human_gaps). They are separated because human
    skills must never drive course recommendations -- they are also the most
    common tokens in the Coursera catalogue and would swamp any ranking.
    """
    technical: list[SkillGap] = []
    human: list[SkillGap] = []

    for requirement in career.required_skills:
        skill = kb.skills.get(requirement.skill)
        if skill is None:  # impossible after KB validation; guard rather than crash
            continue
        student_level = profile.proficiency(requirement.skill)
        gap = requirement.required_level - student_level
        if gap <= EPSILON:
            continue
        entry = SkillGap(
            skill_id=skill.id,
            skill_name=skill.name,
            required_level=requirement.required_level,
            student_level=student_level,
            gap=gap,
            importance=requirement.importance,
            priority=gap * requirement.importance,
            is_human_skill=skill.is_human_skill,
        )
        (human if skill.is_human_skill else technical).append(entry)

    def ranked(gaps: list[SkillGap]) -> tuple[SkillGap, ...]:
        # skill_id breaks ties so the order is stable across runs
        return tuple(sorted(gaps, key=lambda g: (-g.priority, g.skill_id)))

    return ranked(technical), ranked(human)


# --------------------------------------------------------------------------
# scoring one career
# --------------------------------------------------------------------------

def _match_factors(
    profile: StudentProfile, career: Career, kb: KnowledgeBase
) -> tuple[tuple[MatchFactor, ...], tuple[MatchFactor, ...]]:
    """Split the career's requirements into strengths and concerns."""
    strengths: list[MatchFactor] = []
    concerns: list[MatchFactor] = []

    for requirement in career.required_skills:
        skill = kb.skills.get(requirement.skill)
        if skill is None:
            continue
        attainment = skill_attainment(profile.proficiency(requirement.skill), requirement)
        factor = MatchFactor(
            kind="skill",
            id=skill.id,
            label=skill.name,
            value=attainment,
            weight=requirement.importance,
        )
        if attainment >= STRENGTH_ATTAINMENT:
            strengths.append(factor)
        elif attainment < CONCERN_ATTAINMENT:
            concerns.append(factor)

    for tag in career.interest_tags:
        if tag in profile.interests:
            strengths.append(MatchFactor(kind="interest", id=tag, label=tag, value=1.0, weight=1.0))
        else:
            concerns.append(MatchFactor(kind="interest", id=tag, label=tag, value=0.0, weight=1.0))

    for tag in career.work_tags:
        if tag in profile.work_preferences:
            strengths.append(
                MatchFactor(kind="work_preference", id=tag, label=tag, value=1.0, weight=1.0)
            )
        else:
            concerns.append(
                MatchFactor(kind="work_preference", id=tag, label=tag, value=0.0, weight=1.0)
            )

    fit, known = experience_fit_score(profile.experience_level, career.expected_experience)
    if known:
        factor = MatchFactor(
            kind="experience",
            id=career.expected_experience,
            label=career.expected_experience,
            value=fit,
            weight=1.0,
        )
        (strengths if fit >= 1.0 else concerns).append(factor)

    # Most important first; id breaks ties so ordering is deterministic.
    def ranked(factors: list[MatchFactor]) -> tuple[MatchFactor, ...]:
        return tuple(sorted(factors, key=lambda f: (-(f.value * f.weight), f.kind, f.id)))

    return ranked(strengths), ranked(concerns)


def score_career(profile: StudentProfile, career: Career, kb: KnowledgeBase) -> CareerMatch:
    """Score one career against one profile and explain the result structurally."""
    skill = skill_match_score(profile, career)
    interest = tag_match_score(profile.interests, career.interest_tags)
    preference = tag_match_score(profile.work_preferences, career.work_tags)
    experience, experience_known = experience_fit_score(
        profile.experience_level, career.expected_experience
    )

    skill_contribution = WEIGHT_SKILL * skill
    interest_contribution = WEIGHT_INTEREST * interest
    preference_contribution = WEIGHT_PREFERENCE * preference
    experience_contribution = WEIGHT_EXPERIENCE * experience
    score = (
        skill_contribution
        + interest_contribution
        + preference_contribution
        + experience_contribution
    )

    strengths, concerns = _match_factors(profile, career, kb)
    technical_gaps, human_gaps = calculate_skill_gaps(profile, career, kb)

    return CareerMatch(
        career_id=career.id,
        career_name=career.name,
        short_description=career.short_description,
        score=score,
        score_100=round(score * 100),
        breakdown=ScoreBreakdown(
            skill_match=skill,
            interest_match=interest,
            preference_match=preference,
            experience_fit=experience,
            skill_contribution=skill_contribution,
            interest_contribution=interest_contribution,
            preference_contribution=preference_contribution,
            experience_contribution=experience_contribution,
            experience_known=experience_known,
        ),
        strengths=strengths,
        concerns=concerns,
        matching_interests=tuple(t for t in career.interest_tags if t in profile.interests),
        missing_interests=tuple(t for t in career.interest_tags if t not in profile.interests),
        matching_preferences=tuple(t for t in career.work_tags if t in profile.work_preferences),
        missing_preferences=tuple(t for t in career.work_tags if t not in profile.work_preferences),
        skill_gaps=technical_gaps,
        human_skill_gaps=human_gaps,
    )


# --------------------------------------------------------------------------
# cold start + ranking
# --------------------------------------------------------------------------

def missing_information(profile: StudentProfile) -> tuple[MissingInformation, ...]:
    """What the profile still needs before careers can be ranked meaningfully."""
    missing: list[MissingInformation] = []
    if len(profile.skills) < MIN_SKILLS:
        missing.append(MissingInformation("skills", len(profile.skills), MIN_SKILLS))
    if len(profile.interests) < MIN_INTERESTS:
        missing.append(MissingInformation("interests", len(profile.interests), MIN_INTERESTS))
    if len(profile.work_preferences) < MIN_WORK_PREFERENCES:
        missing.append(
            MissingInformation(
                "work_preferences", len(profile.work_preferences), MIN_WORK_PREFERENCES
            )
        )
    return tuple(missing)


def has_enough_information(profile: StudentProfile) -> bool:
    return not missing_information(profile)


def recommend_careers(
    profile: StudentProfile,
    kb: KnowledgeBase,
    limit: int | None = 5,
) -> RecommendationResult:
    """Rank the supported careers for one student.

    Returns a structured "insufficient_profile" result rather than a ranking when
    the profile is too thin -- an invented ranking from nothing is worse than
    admitting we need to keep talking.
    """
    problems = validate_profile(profile, kb)
    if problems:
        raise InvalidProfileError(problems)

    if not has_enough_information(profile):
        return RecommendationResult(
            status="insufficient_profile",
            missing_information=missing_information(profile),
            considered_careers=len(kb.careers),
        )

    matches = [score_career(profile, career, kb) for career in kb.careers.values()]
    # career_id breaks ties so two equally-scored careers never swap between runs
    matches.sort(key=lambda m: (-m.score, m.career_id))
    if limit is not None:
        matches = matches[:limit]

    return RecommendationResult(
        status="ok",
        matches=tuple(matches),
        considered_careers=len(kb.careers),
    )
