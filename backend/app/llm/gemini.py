"""The Gemini implementation of `LLMClient`.

This is the only module in the application that knows a provider exists. It
imports the Google SDK, speaks the Gemini request shape, and translates every
provider concept back into the two verbs the rest of the system uses --
`complete_text` and `complete_json`. Nothing above it changed when the provider
did, which is the point of the seam.

Three translations happen here and nowhere else:

* **Roles.** The application says `user` / `assistant`; Gemini says `user` /
  `model`.
* **System prompt.** Not a message: it goes in `system_instruction`, which keeps
  the counsellor's rules out of the turn history a student could talk over.
* **Schema.** The knowledge base generates one JSON Schema, and it stays the
  single source of the allowed vocabulary. `to_response_schema` adapts it to the
  subset Gemini's `response_json_schema` accepts -- a mechanical rewrite that
  never adds, removes or widens an allowed value.

Errors follow the rule set in `client.py`: the provider's own message is logged
in full server-side and replaced with an opaque `LLMError`, because it can carry
the request body, the model id, account details and, in the worst case, part of
a prompt.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.llm.client import LLMError, LLMMessage, LLMUnavailableError

logger = logging.getLogger(__name__)

#: Gemini calls the assistant "model". Only this mapping knows that.
_ROLE_FOR_GEMINI = {"assistant": "model", "user": "user"}

#: HTTP statuses worth naming separately in the log. Everything else is a
#: generic request failure -- the distinction is for whoever reads the log, not
#: for the student, who sees the same fallback either way.
_UNAUTHORISED = (401, 403)
_RATE_LIMITED = 429


# --------------------------------------------------------------------------
# schema translation
# --------------------------------------------------------------------------

#: What Gemini's `response_json_schema` honours. Anything else is dropped rather
#: than sent: an unsupported keyword is at best ignored and at worst rejected,
#: and neither failure mode is worth risking for a constraint that
#: `app.llm.schemas` re-checks after the response arrives anyway.
_SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
    }
)


def to_response_schema(schema: Any) -> Any:
    """Rewrite a JSON Schema into the subset Gemini accepts.

    Only two things actually need changing, and both are shape rather than
    meaning:

    * A union type -- `{"type": ["object", "null"]}`, which the extraction
      schema uses for an unknown experience level -- becomes an `anyOf`, which
      Gemini does support. "Unknown" has to survive the translation intact: it
      is a real state the engine handles, and a schema that forced a value here
      would make the model guess one.
    * Unsupported keywords are dropped.

    Nothing is added and no enum is widened, so the knowledge base remains the
    only definition of what the model may emit.
    """
    if isinstance(schema, list):
        return [to_response_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    rewritten: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SUPPORTED_KEYWORDS:
            continue
        if key == "type" and isinstance(value, list):
            # Handled below, once the rest of the node has been copied.
            continue
        if key == "properties":
            # A name-to-schema map, not a schema node. Recursing into it as one
            # would filter the property *names* against the keyword list and
            # quietly delete the whole object.
            rewritten[key] = {
                name: to_response_schema(subschema) for name, subschema in value.items()
            }
        elif key == "additionalProperties" and isinstance(value, bool):
            rewritten[key] = value
        else:
            rewritten[key] = to_response_schema(value)

    types = schema.get("type")
    if isinstance(types, list):
        concrete = [t for t in types if t != "null"]
        # The node minus its type key, restated once per concrete type, plus an
        # explicit null branch. One concrete type is the only case the
        # extraction schema produces; the loop covers the general form rather
        # than asserting that stays true.
        branches = [dict(rewritten, type=t) for t in concrete]
        branches.append({"type": "null"})
        return {"anyOf": branches} if len(branches) > 1 else branches[0]

    return rewritten


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------

class GeminiClient:
    """`LLMClient` backed by the Gemini Developer API.

    The SDK is imported inside `__init__` rather than at module scope so the
    application -- and the test suite -- imports cleanly on a machine where the
    package is not installed.
    """

    def __init__(self, *, api_key: str, model: str, timeout: float, max_tokens: int):
        try:
            from google import genai
            from google.genai import errors, types
        except ImportError as error:  # pragma: no cover - depends on the install
            raise LLMUnavailableError("the google-genai SDK is not installed") from error

        self._types = types
        self._errors = errors
        self._model = model
        self._max_tokens = max_tokens
        self._client = genai.Client(
            api_key=api_key,
            # The SDK counts this in milliseconds; the setting is in seconds,
            # because that is what a human writes in a .env file.
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )

    # -- helpers ----------------------------------------------------------

    def _contents(self, messages: list[LLMMessage]) -> list[Any]:
        """Turn conversation turns into Gemini `Content`.

        An unrecognised role is sent as `user` rather than dropped: losing a
        turn silently would leave the model reading a conversation that never
        happened.
        """
        types = self._types
        return [
            types.Content(
                role=_ROLE_FOR_GEMINI.get(message["role"], "user"),
                parts=[types.Part(text=message["content"])],
            )
            for message in messages
        ]

    def _call(self, *, system: str, messages: list[LLMMessage], **config: Any):
        """One request, with every provider failure logged and then anonymised."""
        errors = self._errors
        types = self._types

        contents = self._contents(messages)
        if not contents:
            # Gemini rejects an empty conversation, and there is nothing
            # sensible to invent in its place.
            raise LLMError("llm called with no conversation content")

        try:
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=config.pop("max_tokens", None) or self._max_tokens,
                    **config,
                ),
            )
        except errors.ClientError as error:
            # 4xx. The code separates "your key is wrong" from "you are going
            # too fast", which are very different things to see in a log.
            code = getattr(error, "code", None)
            if code in _UNAUTHORISED:
                logger.error("LLM authentication failed: %s", error)
                raise LLMError("llm authentication failed") from error
            if code == _RATE_LIMITED:
                logger.warning("LLM rate limited: %s", error)
                raise LLMError("llm rate limited") from error
            logger.error("LLM rejected the request (%s): %s", code, error)
            raise LLMError("llm request failed") from error
        except errors.ServerError as error:
            logger.error("LLM server error: %s", error)
            raise LLMError("llm request failed") from error
        except errors.APIError as error:
            # Anything else the SDK classifies as an API failure.
            logger.error("LLM request failed: %s", error)
            raise LLMError("llm request failed") from error
        except Exception as error:
            # Transport-level failures -- timeouts, DNS, a dropped connection --
            # surface as httpx exceptions rather than SDK ones. Catching broadly
            # here is deliberate: an interactive turn must degrade to a fallback,
            # never to a 500 with a stack trace in it.
            logger.warning("LLM call failed: %s: %s", type(error).__name__, error)
            raise LLMError("llm call failed") from error

    @staticmethod
    def _text_of(response: Any) -> str:
        """The response's text, or an empty string.

        `.text` is None when the model produced no text part at all -- a safety
        block, or a turn that hit the output ceiling before writing anything.
        Both are failures, and the callers below treat them as such.
        """
        return (getattr(response, "text", None) or "").strip()

    @staticmethod
    def _finish_reason(response: Any) -> str | None:
        """Why generation stopped, for the log. None when the SDK did not say."""
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        reason = getattr(candidates[0], "finish_reason", None)
        return getattr(reason, "name", None) or (str(reason) if reason else None)

    # -- LLMClient --------------------------------------------------------

    def complete_text(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> str:
        response = self._call(system=system, messages=messages, max_tokens=max_tokens)
        text = self._text_of(response)
        if not text:
            # An empty completion is a failure, not a reply. Returning "" would
            # persist an empty assistant turn into the history.
            logger.error(
                "LLM returned no text (finish_reason=%s)", self._finish_reason(response)
            )
            raise LLMError("llm returned no text")
        return text

    def complete_json(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> Any:
        response = self._call(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            response_json_schema=to_response_schema(schema),
        )
        text = self._text_of(response)
        if not text:
            logger.error(
                "LLM returned no JSON (finish_reason=%s)", self._finish_reason(response)
            )
            raise LLMError("llm returned no json")
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            # Constrained decoding makes this unlikely, not impossible -- a
            # response cut short by the output ceiling lands here.
            logger.error(
                "LLM returned unparseable JSON (finish_reason=%s): %s",
                self._finish_reason(response),
                error,
            )
            raise LLMError("llm returned unparseable json") from error
