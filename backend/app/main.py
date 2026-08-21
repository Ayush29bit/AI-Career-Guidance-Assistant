"""FastAPI application.

    cd backend
    uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs

A modular monolith: one application, one database, a few routers. The
recommendation engine is imported and called, never reimplemented, and the
routers do no scoring of their own.

Every handler below leans on one rule about error responses -- the client is
told what went wrong in terms it can act on, and never sees a stack trace, a
connection string, a SQL statement or an internal path.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api import conversations, health, profile, recommendations
from app.api.deps import get_knowledge_base
from app.config import get_settings
from app.knowledge.loader import KnowledgeBaseError
from app.recommendation.profile import InvalidProfileError

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Parse and validate the knowledge base before serving anything.

    Priming the cache here turns a malformed YAML edit into a failure at boot,
    which is obvious, rather than a 500 on whichever request happens to need it
    first.
    """
    kb = get_knowledge_base()
    logger.info(
        "knowledge base loaded: %d skills, %d careers", len(kb.skills), len(kb.careers)
    )
    yield


app = FastAPI(
    title="AI Career Counsellor",
    description="Conversation-first career guidance for students.",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url=f"{API_PREFIX}/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,  # no auth, so no cookies to carry
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------

@app.exception_handler(SQLAlchemyError)
async def handle_database_error(_: Request, error: SQLAlchemyError) -> JSONResponse:
    """Turn any database failure into a 503 with nothing revealing in it.

    SQLAlchemy's message can include the failing SQL, the host and the user, so
    it is logged for the developer and replaced for the caller.
    """
    logger.exception("database error", exc_info=error)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "the database is unavailable, please try again"},
    )


@app.exception_handler(InvalidProfileError)
async def handle_invalid_profile(_: Request, error: InvalidProfileError) -> JSONResponse:
    """A stored profile no longer fits the knowledge base vocabularies.

    The caller did nothing wrong -- this is stored data that has drifted out of
    step with the taxonomy -- so it is a 500, and the specific problems go to
    the log rather than over the wire.
    """
    logger.error("stored profile is inconsistent with the knowledge base: %s", error.problems)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "the stored profile is not consistent with the knowledge base"},
    )


@app.exception_handler(KnowledgeBaseError)
async def handle_knowledge_base_error(_: Request, error: KnowledgeBaseError) -> JSONResponse:
    logger.error("knowledge base is invalid: %s", error.problems)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "the career knowledge base is unavailable"},
    )


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

app.include_router(health.router, prefix=API_PREFIX)
app.include_router(conversations.router, prefix=API_PREFIX)
app.include_router(profile.router, prefix=API_PREFIX)
app.include_router(recommendations.router, prefix=API_PREFIX)
