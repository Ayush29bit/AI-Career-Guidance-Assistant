"""Liveness and readiness.

Reports whether the application is up and whether its two dependencies -- the
database and the knowledge base -- are actually usable. It deliberately says
nothing about *how* it connects: no URL, host, user or password appears in the
response.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep, get_knowledge_base

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Deliberately free of any connection detail."""

    status: str  # ok | degraded
    service: str
    version: str
    database: str  # ok | unavailable
    knowledge_base: str  # ok | unavailable
    careers: int | None = None
    skills: int | None = None


@router.get("/health", response_model=HealthResponse, summary="Service health")
def health(session: SessionDep, response: Response) -> HealthResponse:
    """Confirm the application is running and its dependencies respond.

    Returns 503 when something the API needs is not usable, so a caller can tell
    "up but broken" from "up and ready" without parsing the body.
    """
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        # The exception text can carry host and user details, so it is not
        # propagated to the client.
        database = "unavailable"

    try:
        kb = get_knowledge_base()
        knowledge_base, careers, skills = "ok", len(kb.careers), len(kb.skills)
    except Exception:
        knowledge_base, careers, skills = "unavailable", None, None

    healthy = database == "ok" and knowledge_base == "ok"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "degraded",
        service="ai-career-counsellor",
        version="0.1.0",
        database=database,
        knowledge_base=knowledge_base,
        careers=careers,
        skills=skills,
    )
