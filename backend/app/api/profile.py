"""The structured student profile.

Shaped for the frontend's supporting panel: skills carry their display name and
category so the UI does not need a second lookup, and the cold-start state is
reported explicitly so the panel can show "still getting to know you" instead of
an empty recommendation list.

The readiness fields come from the engine's own `missing_information`, not from
a rule restated here -- there is exactly one definition of "enough to rank".
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import KnowledgeBaseDep, SessionDep, load_profile_or_404
from app.conversation.profile_bridge import bridge_profile
from app.recommendation.engine import missing_information

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileSkill(BaseModel):
    skill_id: str
    name: str
    category: str
    kind: str
    proficiency: float


class MissingInformation(BaseModel):
    field_name: str
    have: int
    need: int


class ProfileResponse(BaseModel):
    profile_id: uuid.UUID
    #: None means unknown, which the engine treats as neutral. It is never a
    #: stand-in for "beginner".
    experience_level: str | None
    skills: list[ProfileSkill]
    interests: list[str]
    work_preferences: list[str]
    strengths: list[str]
    dislikes: list[str]
    goals: list[str]
    signal_count: int
    ready_for_recommendations: bool
    missing_information: list[MissingInformation]


@router.get(
    "/{profile_id}",
    response_model=ProfileResponse,
    summary="Fetch the structured student profile",
)
def get_profile(
    profile_id: uuid.UUID, session: SessionDep, kb: KnowledgeBaseDep
) -> ProfileResponse:
    db_profile = load_profile_or_404(session, profile_id)
    bridged = bridge_profile(db_profile, kb)
    profile = bridged.profile

    skills = [
        ProfileSkill(
            skill_id=skill_id,
            name=kb.skills[skill_id].name,
            category=kb.skills[skill_id].category,
            kind=kb.skills[skill_id].kind,
            proficiency=proficiency,
        )
        # highest proficiency first, then by id so the order never wobbles
        for skill_id, proficiency in sorted(
            profile.skills.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    missing = missing_information(profile)

    return ProfileResponse(
        profile_id=profile_id,
        experience_level=profile.experience_level,
        skills=skills,
        interests=list(profile.interests),
        work_preferences=list(profile.work_preferences),
        strengths=list(bridged.strengths),
        dislikes=list(bridged.dislikes),
        goals=list(bridged.goals),
        signal_count=profile.known_signal_count,
        ready_for_recommendations=not missing,
        missing_information=[
            MissingInformation(field_name=m.field_name, have=m.have, need=m.need)
            for m in missing
        ],
    )
