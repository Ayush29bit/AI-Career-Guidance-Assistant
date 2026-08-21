"""Test doubles.

The suite must never reach a real provider: a paid API call in a unit test is
slow, non-deterministic and costs money to run CI. `FakeLLM` satisfies the same
`LLMClient` protocol the application depends on, so the code under test is the
production path -- only the far side of the seam changes.

It also records every system prompt it was handed. That is what lets a test
assert the *negative* cases that matter most here: that the briefing the model
received contained the engine's real numbers, and that a career the engine never
ranked was never mentioned to it.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.client import LLMError


class FakeLLM:
    """A scripted `LLMClient`.

    Queues are consumed in order; the last entry repeats once exhausted, so a
    test that sends three messages does not have to script three identical
    replies.
    """

    def __init__(
        self,
        *,
        analyses: list[Any] | None = None,
        replies: list[str] | None = None,
        fail_json: bool = False,
        fail_text: bool = False,
    ):
        self._analyses = list(analyses or [])
        self._replies = list(replies or ["Tell me a bit more about what you enjoy."])
        self.fail_json = fail_json
        self.fail_text = fail_text

        #: Everything the fake was asked, for assertions.
        self.json_systems: list[str] = []
        self.text_systems: list[str] = []
        self.json_schemas: list[dict] = []
        self.histories: list[list[dict]] = []

    # -- LLMClient ---------------------------------------------------------

    def complete_json(self, *, system, messages, schema, max_tokens=None) -> Any:
        self.json_systems.append(system)
        self.json_schemas.append(schema)
        self.histories.append([dict(m) for m in messages])
        if self.fail_json:
            raise LLMError("fake json failure")
        return self._next(self._analyses, default=empty_analysis())

    def complete_text(self, *, system, messages, max_tokens=None) -> str:
        self.text_systems.append(system)
        if self.fail_text:
            raise LLMError("fake text failure")
        return self._next(self._replies, default="...")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _next(queue: list, default):
        if not queue:
            return default
        return queue.pop(0) if len(queue) > 1 else queue[0]

    @property
    def last_briefing(self) -> dict:
        """The JSON briefing block out of the most recent reply prompt.

        Parsed back out of the prompt rather than captured separately, so the
        assertion is about what the model actually saw.
        """
        return briefing_in(self.text_systems[-1])


def briefing_in(system_prompt: str) -> dict:
    """Extract the fenced JSON briefing from a reply prompt."""
    start = system_prompt.index("```json") + len("```json")
    end = system_prompt.index("```", start)
    return json.loads(system_prompt[start:end])


def empty_analysis(intent: str = "general_conversation") -> dict:
    """A well-formed analysis that extracts nothing."""
    return {
        "intent": intent,
        "is_hypothetical": False,
        "careers_mentioned": [],
        "profile_updates": {
            "skills": [],
            "interests": [],
            "work_preferences": [],
            "experience": None,
            "strengths": [],
            "dislikes": [],
            "goals": [],
            "removed_interests": [],
            "removed_work_preferences": [],
            "removed_skills": [],
        },
    }


def analysis(
    intent: str = "profile_update",
    *,
    skills: list[tuple[str, float, str]] = (),
    interests: list[tuple[str, str]] = (),
    work_preferences: list[tuple[str, str]] = (),
    experience: tuple[str, str] | None = None,
    strengths: list[tuple[str, str]] = (),
    dislikes: list[tuple[str, str]] = (),
    goals: list[tuple[str, str]] = (),
    removed_interests: list[str] = (),
    removed_work_preferences: list[str] = (),
    removed_skills: list[str] = (),
    careers: list[str] = (),
    hypothetical: bool = False,
) -> dict:
    """Build an analysis payload from terse tuples, the way a test wants to read.

    Every list is (value, source) pairs -- the source is never defaulted,
    because explicit-versus-inferred is the thing most of these tests are about.
    """
    payload = empty_analysis(intent)
    payload["is_hypothetical"] = hypothetical
    payload["careers_mentioned"] = list(careers)
    updates = payload["profile_updates"]
    updates["skills"] = [
        {"skill_id": skill_id, "proficiency": proficiency, "source": source}
        for skill_id, proficiency, source in skills
    ]
    updates["interests"] = [{"tag": tag, "source": source} for tag, source in interests]
    updates["work_preferences"] = [
        {"tag": tag, "source": source} for tag, source in work_preferences
    ]
    if experience is not None:
        updates["experience"] = {"level": experience[0], "source": experience[1]}
    for key, values in (
        ("strengths", strengths),
        ("dislikes", dislikes),
        ("goals", goals),
    ):
        updates[key] = [{"content": content, "source": source} for content, source in values]
    updates["removed_interests"] = list(removed_interests)
    updates["removed_work_preferences"] = list(removed_work_preferences)
    updates["removed_skills"] = list(removed_skills)
    return payload
