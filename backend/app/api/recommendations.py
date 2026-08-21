"""Career recommendations.

The endpoint is deliberately thin:

    load the stored profile
        -> profile_bridge
        -> recommend_careers()      <- the existing deterministic engine
        -> serialise the result

No scoring, ranking, weighting or tie-breaking happens in this module. Every
number in the response was computed by `app.recommendation.engine`, which is
unchanged. If a score ever looks wrong, there is exactly one place to look.

The response carries the whole explainability payload -- per-component
breakdown, strengths, concerns, matched and missing tags, prioritised gaps --
because that is what the UI renders and what the LLM will later turn into
prose. The LLM is given these numbers; it never produces them.

A profile too thin to rank returns HTTP 200 with `status:
"insufficient_profile"` and the list of what is still needed. That is a real
answer, not an error: the honest response before a student has said enough is
"I don't know yet", and the conversation continues from there.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from app.api.deps import KnowledgeBaseDep, SessionDep, load_profile_or_404
from app.conversation.profile_bridge import to_student_profile
from app.recommendation.engine import RecommendationResult, recommend_careers

router = APIRouter(prefix="/profile", tags=["recommendations"])


# --------------------------------------------------------------------------
# schemas -- one-to-one with the engine's result dataclasses
# --------------------------------------------------------------------------

class MatchFactor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str  # skill | interest | work_preference | experience
    id: str
    label: str
    value: float
    weight: float


class SkillGap(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    skill_id: str
    skill_name: str
    required_level: float
    student_level: float
    gap: float
    importance: float
    #: gap x importance -- what orders the list and drives the roadmap
    priority: float
    is_human_skill: bool


class ScoreBreakdown(BaseModel):
    """Each component in 0..1, plus what it contributed to the final score."""

    model_config = ConfigDict(from_attributes=True)

    skill_match: float
    interest_match: float
    preference_match: float
    experience_fit: float
    skill_contribution: float
    interest_contribution: float
    preference_contribution: float
    experience_contribution: float
    #: False when the student's experience is unknown, so the UI can show the
    #: 10% term as neutral rather than as a judgement.
    experience_known: bool


class CareerMatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    career_id: str
    career_name: str
    short_description: str
    score: float  # 0..1, full precision; this is what ranking used
    score_100: int  # rounded, for display
    breakdown: ScoreBreakdown
    strengths: list[MatchFactor]
    concerns: list[MatchFactor]
    matching_interests: list[str]
    missing_interests: list[str]
    matching_preferences: list[str]
    missing_preferences: list[str]
    #: Technical gaps only; these drive course recommendations.
    skill_gaps: list[SkillGap]
    #: Real gaps, but they must never drive course recommendations.
    human_skill_gaps: list[SkillGap]


class MissingInformation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_name: str
    have: int
    need: int


class RecommendationsResponse(BaseModel):
    profile_id: uuid.UUID
    status: str  # ok | insufficient_profile
    considered_careers: int
    matches: list[CareerMatch]
    missing_information: list[MissingInformation]


def build_recommendations_response(
    profile_id: uuid.UUID, result: RecommendationResult
) -> RecommendationsResponse:
    """Serialise an engine result.

    Split out from the endpoint so the conversation endpoint can return the very
    same ranking it just handed the LLM to explain -- one computation, one set
    of numbers, no chance of the prose and the panel disagreeing.
    """
    return RecommendationsResponse(
        profile_id=profile_id,
        status=result.status,
        considered_careers=result.considered_careers,
        matches=[CareerMatch.model_validate(m) for m in result.matches],
        missing_information=[
            MissingInformation.model_validate(m) for m in result.missing_information
        ],
    )


@router.get(
    "/{profile_id}/recommendations",
    response_model=RecommendationsResponse,
    summary="Rank careers for a stored profile",
)
def get_recommendations(
    profile_id: uuid.UUID,
    session: SessionDep,
    kb: KnowledgeBaseDep,
    limit: int = Query(5, ge=1, le=20, description="How many ranked careers to return."),
) -> RecommendationsResponse:
    db_profile = load_profile_or_404(session, profile_id)
    profile = to_student_profile(db_profile, kb)
    return build_recommendations_response(profile_id, recommend_careers(profile, kb, limit=limit))
