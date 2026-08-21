"""Shared FastAPI dependencies and lookup helpers.

Small on purpose: a cached knowledge base, two typed dependency aliases, and the
two "load it or 404" helpers that more than one router needs. Anything larger
belongs in the module that actually uses it.

This is a separate module rather than part of `app.api.__init__` because the
routers import from it and `app.main` imports the routers; putting it in the
package `__init__` would make that circular.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import models
from app.database.session import get_session
from app.knowledge.loader import KnowledgeBase, load_knowledge_base


@lru_cache(maxsize=1)
def get_knowledge_base() -> KnowledgeBase:
    """The knowledge base, parsed once per process.

    The YAML is a few hundred lines and never changes at runtime, so re-reading
    and re-validating it on every request would be pure waste. `app.main`
    primes this during startup so a malformed edit fails at boot rather than on
    the first request that needs it.
    """
    return load_knowledge_base()


SessionDep = Annotated[Session, Depends(get_session)]
KnowledgeBaseDep = Annotated[KnowledgeBase, Depends(get_knowledge_base)]


def load_profile_or_404(session: Session, profile_id: uuid.UUID) -> models.StudentProfile:
    """Fetch a student profile with everything the bridge reads already loaded.

    selectinload keeps this to a handful of queries instead of one per
    collection per profile.
    """
    profile = session.execute(
        select(models.StudentProfile)
        .where(models.StudentProfile.id == profile_id)
        .options(
            selectinload(models.StudentProfile.skills),
            selectinload(models.StudentProfile.tags),
            selectinload(models.StudentProfile.entries),
        )
    ).scalar_one_or_none()

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"profile {profile_id} not found",
        )
    return profile


def load_conversation_or_404(
    session: Session, conversation_id: uuid.UUID, *, with_messages: bool = False
) -> models.Conversation:
    statement = select(models.Conversation).where(models.Conversation.id == conversation_id)
    if with_messages:
        statement = statement.options(selectinload(models.Conversation.messages))

    conversation = session.execute(statement).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation {conversation_id} not found",
        )
    return conversation
