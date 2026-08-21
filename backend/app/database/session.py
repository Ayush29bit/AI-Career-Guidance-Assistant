"""Engine and session management.

The engine is created lazily and cached, so importing this module never requires
DATABASE_URL to be set. That matters because `app.database.models` is imported by
Alembic and (indirectly) by tests that have no database at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide engine, created on first use.

    pool_pre_ping costs one cheap round trip per checkout and avoids the classic
    "server closed the connection unexpectedly" after an idle spell in
    development.
    """
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        # Attributes stay readable after commit, so a request handler can still
        # serialise an object it just saved.
        expire_on_commit=False,
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope: commit on success, roll back on any exception."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Per-request session, shaped as a FastAPI dependency for the API phase.

    Deliberately does not commit: a request handler decides whether its work
    should be persisted.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
