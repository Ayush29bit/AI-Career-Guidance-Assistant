"""The one seam between this application and an LLM provider.

Everything above this module speaks in two verbs -- "give me text" and "give me
JSON matching this schema" -- and knows nothing about the provider, the SDK, or
the wire format. That is the whole point of the abstraction: swapping providers
should touch this file and nothing else.

It is deliberately not a multi-provider framework. There is one implementation
-- `app.llm.gemini.GeminiClient` -- and this module only names it in
`build_llm_client`. Swapping providers means writing a second class that
satisfies `LLMClient` and changing one line here, not building a registry today
for a need that does not exist.

Keeping the protocol here and the implementation next door is what makes the
rest of the application provider-independent: the conversation service, prompts,
schemas and merge logic import from this module and never from a provider's SDK.

Two rules shape the error handling:

* **The provider's message never reaches the user.** It can contain the request
  body, the model id, account details -- and, in the worst case, part of a
  prompt. Provider failures are logged in full server-side and re-raised as a
  bare `LLMError` for the caller to turn into a fallback.
* **A missing key is not a crash.** `LLM_API_KEY` unset is an ordinary
  deployment state: the API still runs and the conversation layer falls back.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, TypedDict

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """A provider call failed. Carries nothing the caller may show a user."""


class LLMUnavailableError(LLMError):
    """No LLM is configured. A deployment state, not a bug."""


class LLMMessage(TypedDict):
    """One conversation turn as the provider sees it. Roles: user | assistant."""

    role: str
    content: str


class LLMClient(Protocol):
    """What the conversation layer needs from a provider. Nothing more.

    Note what is absent: no scoring call, no ranking call, no "rate this career"
    helper. The interface offers no way to ask the model for a number the engine
    owns.
    """

    def complete_text(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> str:
        """Free-form natural language. Used for the counsellor's reply."""
        ...

    def complete_json(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> Any:
        """A response constrained to `schema`, returned as parsed JSON.

        Shape validation still happens in `app.llm.schemas`. This method
        guarantees only that the return value is valid JSON.
        """
        ...


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------

def build_llm_client(settings: Settings | None = None) -> LLMClient | None:
    """The configured client, or None when no LLM is configured.

    None rather than an exception: an unconfigured LLM is a state the whole
    application is built to tolerate, and making every caller wrap construction
    in a try block would say otherwise. Callers that receive None fall back.
    """
    settings = settings or get_settings()
    if not settings.llm_configured:
        logger.info("no LLM_API_KEY configured; the conversation layer will fall back")
        return None

    assert settings.llm_api_key is not None  # implied by llm_configured

    # Imported here, not at module scope: this module defines the protocol every
    # other module depends on, and it must stay importable on a machine with no
    # provider SDK installed.
    from app.llm.gemini import GeminiClient

    try:
        return GeminiClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )
    except LLMUnavailableError as error:
        # Configured but unusable -- the SDK is missing. Worth a loud log and a
        # fallback, not a dead application.
        logger.error("LLM is configured but unavailable: %s", error)
        return None
