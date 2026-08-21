"""One turn of the conversation, start to finish.

This module is the orchestration seam described in docs/AI.md §18:

    user message
        -> LLM              understand
        -> merge            record
        -> engine           decide
        -> LLM              explain

It calls the LLM twice and the recommendation engine as many times as the turn
needs, and it computes nothing itself. Every figure in the reply came from
`app.recommendation`; every fact written to the profile came from a validated
extraction. Between those two halves this module only routes.

Three failure rules shape the control flow, and they are the reason the steps
are ordered the way they are:

* **The user's message is always saved.** It is real; it is written first and
  survives every failure below it.
* **A failed turn never corrupts the profile.** The profile is written only from
  an extraction that passed both validation layers. A malformed one leaves the
  stored profile byte-identical.
* **A fallback is never persisted as a counsellor turn.** If the reply call
  fails, the student sees an apology and the history stays honest: no assistant
  message is written, so the next turn does not read back words the counsellor
  never said.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.conversation import context
from app.conversation.merge import MergeReport, merge_profile_extraction
from app.conversation.profile_bridge import BridgedProfile, bridge_profile
from app.database import models
from app.knowledge.loader import KnowledgeBase
from app.llm import prompts
from app.llm.client import LLMClient, LLMError, LLMMessage
from app.llm.schemas import (
    COURSE_BACKED_INTENTS,
    FilteredAnalysis,
    ProfileExtraction,
    TurnAnalysis,
    parse_turn_analysis,
    turn_analysis_schema,
)
from app.recommendation.courses import (
    CourseCatalogue,
    CourseRecommendation,
    recommend_courses_for_gaps,
    target_difficulty_level,
)
from app.recommendation.engine import (
    CareerMatch,
    RecommendationResult,
    recommend_careers,
    score_career,
)
from app.recommendation.profile import InvalidProfileError, StudentProfile
from app.recommendation.roadmap import Roadmap, build_roadmap

logger = logging.getLogger(__name__)

#: How many earlier turns are replayed to the model. Enough to keep a thread of
#: conversation coherent; the structured profile carries the durable facts, so
#: there is nothing to gain from replaying the whole history (docs/AI.md §7).
HISTORY_TURNS = 12

#: Turn outcomes, reported to the client so it can render a failure as a failure
#: rather than as counsellor advice.
STATUS_OK = "ok"
STATUS_LLM_UNAVAILABLE = "llm_unavailable"
STATUS_LLM_ERROR = "llm_error"

#: The intent used when the analysis call fails. It routes to plain
#: conversation, which is the only honest thing to do without an intent.
DEFAULT_INTENT = "general_conversation"

#: Intents that answer a question about one specific career, so the briefing
#: needs that career's full detail rather than just the top of the ranking.
_FOCUSED_INTENTS = frozenset(
    {"why_recommendation", "skill_gap", "course_recommendation", "roadmap"}
)


@dataclass(frozen=True)
class TurnResult:
    """Everything one turn produced, for the API layer to serialise.

    `assistant_message` is None exactly when `status` is not `ok` -- that pairing
    is the contract that keeps a fallback out of the persisted history.
    """

    user_message: models.Message
    assistant_message: models.Message | None
    reply: str
    intent: str
    status: str
    bridged: BridgedProfile
    recommendations: RecommendationResult | None = None
    briefing: dict[str, Any] | None = None
    merge_report: MergeReport | None = None
    rejected: tuple[str, ...] = ()

    @property
    def persisted(self) -> bool:
        return self.assistant_message is not None


#: Lazily provides the course catalogue. A callable rather than the catalogue
#: itself because loading it parses a 600-row CSV, and most turns never need it.
CatalogueProvider = Callable[[], CourseCatalogue]


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def load_history(
    session: Session, conversation_id, limit: int = HISTORY_TURNS
) -> list[LLMMessage]:
    """The last few turns, oldest first, as provider messages.

    Ordered by id, which is a total order over the conversation. System rows are
    skipped: none are written today, and if any ever are they will be application
    bookkeeping rather than something to replay at the model.
    """
    rows = (
        session.execute(
            select(models.Message)
            .where(models.Message.conversation_id == conversation_id)
            .order_by(models.Message.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        LLMMessage(role=row.role, content=row.content)
        for row in reversed(rows)
        if row.role in ("user", "assistant")
    ]


# --------------------------------------------------------------------------
# the turn
# --------------------------------------------------------------------------

def handle_user_message(
    session: Session,
    conversation: models.Conversation,
    db_profile: models.StudentProfile,
    content: str,
    kb: KnowledgeBase,
    llm: LLMClient | None,
    catalogue_provider: CatalogueProvider | None = None,
) -> TurnResult:
    """Run one full turn and commit whatever legitimately happened.

    Commits exactly once, at the end, so a turn is all-or-nothing apart from the
    deliberate exception: when the reply call fails, the user's message and any
    profile updates are still committed. Both are true things that happened.
    """
    user_message = models.Message(
        conversation_id=conversation.id, role="user", content=content
    )
    session.add(user_message)
    session.flush()  # give it an id before anything else can fail

    bridged = bridge_profile(db_profile, kb)

    if llm is None:
        session.commit()
        session.refresh(user_message)
        logger.info("no LLM configured; returning a fallback for conversation %s", conversation.id)
        return TurnResult(
            user_message=user_message,
            assistant_message=None,
            reply=prompts.FALLBACK_REPLY,
            intent=DEFAULT_INTENT,
            status=STATUS_LLM_UNAVAILABLE,
            bridged=bridged,
        )

    history = load_history(session, conversation.id)

    # ---- 1. understand --------------------------------------------------
    filtered, analysis_failed = _analyse(llm, kb, bridged, history)

    if filtered is None and analysis_failed == "provider":
        # Nothing was understood and nothing will be said. Keep the message.
        session.commit()
        session.refresh(user_message)
        return TurnResult(
            user_message=user_message,
            assistant_message=None,
            reply=prompts.FALLBACK_REPLY,
            intent=DEFAULT_INTENT,
            status=STATUS_LLM_ERROR,
            bridged=bridged,
        )

    analysis = filtered.analysis if filtered else TurnAnalysis(intent=DEFAULT_INTENT)
    rejected = filtered.rejected if filtered else ()

    # ---- 2. record ------------------------------------------------------
    report: MergeReport | None = None
    if filtered is not None and not analysis.is_hypothetical:
        report = merge_profile_extraction(session, db_profile, analysis.profile_updates)
        session.flush()
        session.expire(db_profile)
        bridged = bridge_profile(db_profile, kb)
        logger.info("profile merge for %s: %s", db_profile.id, report.summary())

    # ---- 3. decide ------------------------------------------------------
    briefing, result = _build_briefing(
        analysis=analysis,
        bridged=bridged,
        kb=kb,
        catalogue_provider=catalogue_provider,
        extraction_failed=filtered is None,
    )

    # ---- 4. explain -----------------------------------------------------
    try:
        reply = llm.complete_text(
            system=prompts.reply_system_prompt(briefing), messages=history
        )
    except LLMError:
        # Already logged with the provider's own message inside the client.
        session.commit()  # the profile updates above really happened
        session.refresh(user_message)
        return TurnResult(
            user_message=user_message,
            assistant_message=None,
            reply=(
                prompts.FALLBACK_REPLY_AFTER_EXTRACTION
                if report and report.changed
                else prompts.FALLBACK_REPLY
            ),
            intent=analysis.intent,
            status=STATUS_LLM_ERROR,
            bridged=bridged,
            recommendations=result,
            briefing=briefing,
            merge_report=report,
            rejected=rejected,
        )

    assistant_message = models.Message(
        conversation_id=conversation.id, role="assistant", content=reply
    )
    session.add(assistant_message)
    session.commit()
    session.refresh(user_message)
    session.refresh(assistant_message)

    return TurnResult(
        user_message=user_message,
        assistant_message=assistant_message,
        reply=reply,
        intent=analysis.intent,
        status=STATUS_OK,
        bridged=bridged,
        recommendations=result,
        briefing=briefing,
        merge_report=report,
        rejected=rejected,
    )


# --------------------------------------------------------------------------
# understand
# --------------------------------------------------------------------------

def _analyse(
    llm: LLMClient, kb: KnowledgeBase, bridged: BridgedProfile, history: list[LLMMessage]
) -> tuple[FilteredAnalysis | None, str | None]:
    """Extract structure from the latest turn.

    Returns (result, failure_kind). The two failure kinds are handled very
    differently by the caller, which is why they are distinguished here rather
    than collapsed into None:

    * `"provider"` -- the call itself failed. The reply call will fail too, so
      there is no point continuing.
    * `"invalid"`  -- the model answered with something unusable. The profile is
      left alone, but the conversation can still continue: a counsellor who
      misheard asks again rather than going silent.
    """
    try:
        payload = llm.complete_json(
            system=prompts.analysis_system_prompt(
                kb, context.profile_summary(bridged, kb)
            ),
            messages=history,
            schema=turn_analysis_schema(kb),
        )
    except LLMError:
        return None, "provider"

    try:
        filtered = parse_turn_analysis(payload, kb)
    except ValidationError as error:
        # The payload is logged at debug rather than info: it is the student's
        # own words reflected back, and the error itself is what identifies the
        # problem.
        logger.warning("LLM extraction did not validate; keeping the profile unchanged")
        logger.debug("invalid extraction: %s", error)
        return None, "invalid"

    if filtered.had_rejections:
        logger.warning("dropped out-of-vocabulary extraction values: %s", filtered.rejected)
    return filtered, None


# --------------------------------------------------------------------------
# decide
# --------------------------------------------------------------------------

def _build_briefing(
    *,
    analysis: TurnAnalysis,
    bridged: BridgedProfile,
    kb: KnowledgeBase,
    catalogue_provider: CatalogueProvider | None,
    extraction_failed: bool,
) -> tuple[dict[str, Any], RecommendationResult | None]:
    """Run whatever deterministic logic this turn needs, and package the result.

    The ranking is computed on every turn, not only when the student asks for
    one: it is what tells the reply whether the profile is ready, and therefore
    whether naming a strongest match is honest yet.
    """
    profile = bridged.profile
    notes: list[str] = []
    if extraction_failed:
        notes.append(
            "This turn could not be understood well enough to update the profile. "
            "Ask the student to say it another way."
        )

    try:
        result = recommend_careers(profile, kb, limit=context.TOP_MATCHES)
    except InvalidProfileError:
        # The stored profile drifted out of step with the taxonomy. Handled by
        # the application's error handler for API callers; here the honest move
        # is to keep talking with no ranking rather than to fail the turn.
        logger.exception("stored profile is inconsistent with the knowledge base")
        briefing = context.build_briefing(
            intent=analysis.intent,
            bridged=bridged,
            kb=kb,
            notes=tuple(notes + ["No recommendation data is available for this turn."]),
        )
        return briefing, None

    focus = _focus_careers(analysis, result, profile, kb)
    courses: tuple[CourseRecommendation, ...] = ()
    roadmap: Roadmap | None = None

    if analysis.intent in COURSE_BACKED_INTENTS and focus and catalogue_provider is not None:
        courses, roadmap = _course_context(
            intent=analysis.intent,
            career_match=focus[0],
            profile=profile,
            kb=kb,
            catalogue=catalogue_provider(),
        )
        if analysis.intent == "course_recommendation" and not courses:
            notes.append(
                "The catalogue has no course for these gaps. Say so rather than "
                "suggesting one."
            )

    scenario: dict[str, Any] | None = None
    scenario_result: RecommendationResult | None = None
    if analysis.is_hypothetical and not analysis.profile_updates.is_empty:
        scenario, scenario_result = _scenario_context(analysis.profile_updates, profile, kb)

    briefing = context.build_briefing(
        intent=analysis.intent,
        bridged=bridged,
        kb=kb,
        result=result,
        focus_careers=focus,
        courses=courses,
        roadmap=roadmap,
        scenario=scenario,
        scenario_result=scenario_result,
        notes=tuple(notes),
    )
    return briefing, result


def _focus_careers(
    analysis: TurnAnalysis,
    result: RecommendationResult,
    profile: StudentProfile,
    kb: KnowledgeBase,
) -> tuple[CareerMatch, ...]:
    """Which careers this turn needs in full detail.

    Careers the student named win, in the order they named them. Failing that, a
    question about "it" is about the current top match. Both paths return engine
    output -- a career is never described from anywhere else.

    "Compare X and Y" is a fair question even when Y is nowhere near the top of
    the ranking, so a named career the ranking did not surface is scored on
    demand with `score_career` -- the same function the ranking itself uses, so
    the two can never disagree.
    """
    if not result.is_ok:
        return ()

    by_id = {match.career_id: match for match in result.matches}

    if analysis.careers_mentioned:
        return tuple(
            by_id.get(career_id) or score_career(profile, kb.careers[career_id], kb)
            for career_id in analysis.careers_mentioned
            if career_id in kb.careers
        )

    if analysis.intent in _FOCUSED_INTENTS and result.matches:
        return (result.matches[0],)

    return ()


def _course_context(
    *,
    intent: str,
    career_match: CareerMatch,
    profile: StudentProfile,
    kb: KnowledgeBase,
    catalogue: CourseCatalogue,
) -> tuple[tuple[CourseRecommendation, ...], Roadmap | None]:
    """Courses, a roadmap, or both -- all from the existing deterministic logic.

    Human-skill gaps are excluded by the engine before they reach here, so they
    can never drive a course recommendation.
    """
    career = kb.careers[career_match.career_id]

    if intent == "roadmap":
        roadmap = build_roadmap(profile, career, kb, catalogue)
        # The roadmap already carries its own per-stage courses; a second flat
        # list would invite the reply to mix two orderings.
        return (), roadmap

    courses = recommend_courses_for_gaps(
        career_match.skill_gaps,
        catalogue,
        kb,
        target_level=target_difficulty_level(profile, career.expected_experience),
        limit=context.TOP_COURSES,
    )
    return courses, None


def _scenario_context(
    updates: ProfileExtraction, profile: StudentProfile, kb: KnowledgeBase
) -> tuple[dict[str, Any], RecommendationResult | None]:
    """Score a hypothetical without touching the stored profile."""
    scenario = context.describe_scenario(updates)
    try:
        hypothetical = context.scenario_profile(profile, updates)
        result = recommend_careers(hypothetical, kb, limit=context.TOP_MATCHES)
    except InvalidProfileError:
        logger.exception("hypothetical profile failed validation")
        return scenario, None
    return scenario, result
