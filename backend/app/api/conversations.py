"""Conversations and messages.

Anonymous throughout: creating a conversation creates an empty student profile
alongside it and hands back both ids. There is no account and no login; the ids
are the only handle a client needs.

The message endpoint stores the user's turn and returns a placeholder reply. The
placeholder is **not** persisted -- writing invented assistant text into the
history would mean the LLM layer later reads back words it never produced. The
request and response shapes are the ones the real conversational layer will use,
so the frontend can build against this contract now.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import SessionDep, load_conversation_or_404
from app.database import models

router = APIRouter(prefix="/conversations", tags=["conversations"])

PLACEHOLDER_REPLY = (
    "Thanks -- I've saved that. I can't reply properly yet: the conversational "
    "layer isn't connected in this build."
)


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


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


class PlaceholderReply(BaseModel):
    """Stands in for the counsellor's turn until the LLM layer exists.

    `status` is machine-readable so the frontend can render this differently
    from a real reply rather than having to match on the text.
    """

    role: Literal["assistant"] = "assistant"
    status: Literal["not_implemented"] = "not_implemented"
    content: str = PLACEHOLDER_REPLY
    persisted: bool = False


class MessageCreated(BaseModel):
    conversation_id: uuid.UUID
    message: MessageOut
    reply: PlaceholderReply


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
    response_model=MessageCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Post a user message",
)
def create_message(
    conversation_id: uuid.UUID, payload: MessageCreate, session: SessionDep
) -> MessageCreated:
    """Store the user's message and acknowledge it.

    No LLM is called. The reply is a placeholder and is not written to the
    history.
    """
    conversation = load_conversation_or_404(session, conversation_id)

    message = models.Message(
        conversation_id=conversation.id, role="user", content=payload.content
    )
    session.add(message)
    session.commit()
    session.refresh(message)

    return MessageCreated(
        conversation_id=conversation.id,
        message=MessageOut.model_validate(message),
        reply=PlaceholderReply(),
    )
