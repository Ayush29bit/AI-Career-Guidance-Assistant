"""Conversations and messages.

Anonymous throughout: creating a conversation creates an empty student profile
alongside it and hands back both ids. There is no account and no login; the ids
are the only handle a client needs.

Posting a message runs a full counselling turn -- understand, record, rank,
explain -- in `app.conversation.service`. This module does none of that work. It
validates the request, hands off, and serialises what came back, so there is
exactly one implementation of a turn and exactly one chat endpoint.

Two shapes in the response deserve a note.

`user_message` and `message` are both returned. The first is the student's own
turn, echoed back with the id and timestamp the database assigned; the second is
the counsellor's. Keeping them separate means a client never has to guess which
turn it is looking at.

`message.persisted` is false exactly when the LLM could not be reached. The
fallback text is shown to the student but is not written to the history, so the
next turn does not read back words the counsellor never said. `status` says why
in machine-readable terms, so a client can render a failure as a failure rather
than as advice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import (
    KnowledgeBaseDep,
    LLMClientDep,
    SessionDep,
    get_course_catalogue,
    load_conversation_or_404,
    load_profile_or_404,
)
from app.api.profile import ProfileResponse, build_profile_response
from app.api.recommendations import RecommendationsResponse, build_recommendations_response
from app.conversation.service import handle_user_message
from app.database import models

router = APIRouter(prefix="/conversations", tags=["conversations"])


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


class AssistantReply(BaseModel):
    """The counsellor's turn, or the fallback that stood in for it.

    `id` and `created_at` are None precisely when `persisted` is false: there is
    no row, because nothing was written.
    """

    role: Literal["assistant"] = "assistant"
    content: str
    persisted: bool
    id: int | None = None
    created_at: datetime | None = None


class ConversationCreated(BaseModel):
    conversation_id: uuid.UUID
    profile_id: uuid.UUID
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation_id: uuid.UUID
    profile_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    messages: list[MessageOut]


class MessageCreate(BaseModel):
    """One user turn.

    The length cap is a guard against a runaway paste, not a product rule. The
    validator rejects whitespace-only content, which `min_length` alone would
    happily accept.
    """

    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message content cannot be blank")
        return stripped


class TurnResponse(BaseModel):
    """Everything the turn produced.

    The profile and the recommendations are the ones this turn actually used, so
    a client rendering the panel alongside the reply is guaranteed to be showing
    the same numbers the counsellor was talking about.
    """

    conversation_id: uuid.UUID
    user_message: MessageOut
    message: AssistantReply
    intent: str
    #: ok | llm_unavailable | llm_error
    status: str
    profile: ProfileResponse
    #: None when the profile is still too thin for the engine to rank anything,
    #: or when the turn failed before the engine ran.
    recommendations: RecommendationsResponse | None = None


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

@router.post(
    "",
    response_model=ConversationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Start an anonymous conversation",
)
def create_conversation(session: SessionDep) -> ConversationCreated:
    """Create an empty profile and a conversation pointing at it.

    The profile starts genuinely empty -- no experience level, no skills. The
    engine reports "insufficient_profile" for it, which is the honest answer
    before the student has said anything.
    """
    profile = models.StudentProfile()
    session.add(profile)
    session.flush()  # assign the profile id before the conversation references it

    conversation = models.Conversation(student_profile_id=profile.id)
    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    return ConversationCreated(
        conversation_id=conversation.id,
        profile_id=profile.id,
        created_at=conversation.created_at,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Fetch a conversation and its message history",
)
def get_conversation(conversation_id: uuid.UUID, session: SessionDep) -> ConversationDetail:
    conversation = load_conversation_or_404(session, conversation_id, with_messages=True)
    # The relationship is ordered by Message.id, which is a total order over the
    # conversation -- created_at can tie, insertion order cannot.
    messages = [MessageOut.model_validate(m) for m in conversation.messages]

    return ConversationDetail(
        conversation_id=conversation.id,
        profile_id=conversation.student_profile_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(messages),
        messages=messages,
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=TurnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a message to the counsellor",
)
def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    session: SessionDep,
    kb: KnowledgeBaseDep,
    llm: LLMClientDep,
) -> TurnResponse:
    """Run one counselling turn.

    A conversation with no profile attached is refused rather than quietly
    given a new one. The link is nullable so a conversation can exist before
    there is anything to record, and `SET NULL` on profile deletion can leave it
    that way -- but a counselling turn with nowhere to put what it learns is a
    conflict, not something to paper over.
    """
    conversation = load_conversation_or_404(session, conversation_id)
    if conversation.student_profile_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this conversation has no student profile attached",
        )
    db_profile = load_profile_or_404(session, conversation.student_profile_id)

    turn = handle_user_message(
        session=session,
        conversation=conversation,
        db_profile=db_profile,
        content=payload.content,
        kb=kb,
        llm=llm,
        # Passed as a callable, not a value: it parses the course dataset, and
        # only a course or roadmap turn ever needs it.
        catalogue_provider=get_course_catalogue,
    )

    recommendations = None
    if turn.recommendations is not None:
        recommendations = build_recommendations_response(db_profile.id, turn.recommendations)

    return TurnResponse(
        conversation_id=conversation.id,
        user_message=MessageOut.model_validate(turn.user_message),
        message=AssistantReply(
            content=turn.reply,
            persisted=turn.persisted,
            id=turn.assistant_message.id if turn.assistant_message else None,
            created_at=(
                turn.assistant_message.created_at if turn.assistant_message else None
            ),
        ),
        intent=turn.intent,
        status=turn.status,
        profile=build_profile_response(db_profile.id, turn.bridged, kb),
        recommendations=recommendations,
    )
