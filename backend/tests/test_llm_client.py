"""The provider seam.

No network. The SDK's real exception classes, request types and response types
are used -- they construct fine offline -- and only the transport is replaced,
so what is exercised is the actual translation code rather than a paraphrase of
it.

Three things are ours rather than the provider's, and they are what these tests
cover:

* the schema rewrite that adapts the knowledge base's generated JSON Schema to
  what Gemini's `response_json_schema` accepts, without widening it;
* the request shape -- roles, system instruction, output ceiling;
* how a failure is translated, and what a caller is allowed to learn from it.

The rule throughout: a provider's own message never crosses this boundary. It
can carry the request body, the model id, account details, and in the worst case
part of a prompt, so it is logged and replaced.
"""

from __future__ import annotations

import pathlib
import unittest

from google.genai import errors, types

from app.config import Settings
from app.knowledge.loader import load_knowledge_base
from app.llm.client import LLMError, build_llm_client
from app.llm.gemini import GeminiClient, to_response_schema
from app.llm.schemas import turn_analysis_schema

KB = load_knowledge_base()


def api_error(cls, code: int, message: str = "boom"):
    """One of the SDK's own errors, built the way the SDK builds it."""
    return cls(code, {"error": {"code": code, "message": message, "status": "ERROR"}})


def response_with(text: str | None, finish_reason: str = "STOP"):
    """A real `GenerateContentResponse`, so `.text` behaves as it does in production."""
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts),
                finish_reason=finish_reason,
            )
        ]
    )


class _Models:
    """Stands in for `client.models`, recording calls instead of making them."""

    def __init__(self, stub):
        self._stub = stub

    def generate_content(self, **kwargs):
        self._stub.calls.append(kwargs)
        if self._stub.raises is not None:
            raise self._stub.raises
        return self._stub.response


class StubTransport:
    """The only thing replaced: the object that would have talked to Google."""

    def __init__(self, response=None, raises=None):
        self.response = response if response is not None else response_with("hello")
        self.raises = raises
        self.calls: list[dict] = []
        self.models = _Models(self)


def client_with(stub: StubTransport, model: str = "test-model") -> GeminiClient:
    """A GeminiClient wired to the stub, with the SDK's real types and errors."""
    instance = GeminiClient.__new__(GeminiClient)
    instance._types = types
    instance._errors = errors
    instance._model = model
    instance._max_tokens = 100
    instance._client = stub
    return instance


USER = [{"role": "user", "content": "hello"}]


# --------------------------------------------------------------------------
# schema translation
# --------------------------------------------------------------------------

class TestSchemaTranslation(unittest.TestCase):
    """The knowledge base stays the only definition of the allowed vocabulary."""

    def setUp(self):
        self.original = turn_analysis_schema(KB)
        self.adapted = to_response_schema(self.original)
        self.updates = self.adapted["properties"]["profile_updates"]["properties"]

    def test_the_object_survives_the_rewrite(self):
        """Regression: property names are not schema keywords and must not be filtered."""
        self.assertEqual(
            list(self.adapted["properties"]),
            list(self.original["properties"]),
        )
        self.assertEqual(self.adapted["required"], self.original["required"])

    def test_skill_ids_stay_a_closed_enum_of_the_taxonomy(self):
        enum = self.updates["skills"]["items"]["properties"]["skill_id"]["enum"]
        self.assertEqual(set(enum), set(KB.skills))

    def test_tag_enums_are_unchanged(self):
        self.assertEqual(
            self.updates["interests"]["items"]["properties"]["tag"]["enum"],
            list(KB.interest_tags),
        )
        self.assertEqual(
            self.updates["work_preferences"]["items"]["properties"]["tag"]["enum"],
            list(KB.work_tags),
        )

    def test_career_ids_stay_a_closed_enum(self):
        enum = self.adapted["properties"]["careers_mentioned"]["items"]["enum"]
        self.assertEqual(set(enum), set(KB.careers))

    def test_bounds_survive(self):
        proficiency = self.updates["skills"]["items"]["properties"]["proficiency"]
        self.assertEqual((proficiency["minimum"], proficiency["maximum"]), (0.0, 1.0))

    def test_unknown_experience_stays_expressible(self):
        """A schema that forced a level would make the model guess one."""
        branches = self.updates["experience"]["anyOf"]
        self.assertIn({"type": "null"}, branches)
        object_branch = next(b for b in branches if b.get("type") == "object")
        self.assertEqual(
            object_branch["properties"]["level"]["enum"], list(KB.experience_levels)
        )

    def test_unsupported_keywords_are_dropped(self):
        adapted = to_response_schema(
            {"type": "string", "pattern": "^x", "$comment": "hi", "enum": ["a"]}
        )
        self.assertEqual(adapted, {"type": "string", "enum": ["a"]})

    def test_nothing_is_added_or_widened(self):
        """Every enum in the adapted schema is one the knowledge base produced."""

        def enums(node, found=None):
            found = [] if found is None else found
            if isinstance(node, dict):
                if "enum" in node:
                    found.append(tuple(node["enum"]))
                for value in node.values():
                    enums(value, found)
            elif isinstance(node, list):
                for value in node:
                    enums(value, found)
            return found

        self.assertEqual(sorted(enums(self.adapted)), sorted(enums(self.original)))


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------

class TestRequestShape(unittest.TestCase):
    def test_the_system_prompt_is_an_instruction_not_a_turn(self):
        """Keeping it out of `contents` keeps it out of what a student can talk over."""
        stub = StubTransport()
        client_with(stub).complete_text(system="be warm", messages=USER)
        call = stub.calls[0]
        self.assertEqual(call["config"].system_instruction, "be warm")
        self.assertEqual(len(call["contents"]), 1)

    def test_the_assistant_role_is_translated(self):
        stub = StubTransport()
        client_with(stub).complete_text(
            system="s",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again"},
            ],
        )
        contents = stub.calls[0]["contents"]
        self.assertEqual([c.role for c in contents], ["user", "model", "user"])
        self.assertEqual([c.parts[0].text for c in contents], ["hi", "hello", "again"])

    def test_the_configured_model_is_used(self):
        stub = StubTransport()
        client_with(stub, model="gemini-test").complete_text(system="s", messages=USER)
        self.assertEqual(stub.calls[0]["model"], "gemini-test")

    def test_an_empty_conversation_is_refused_rather_than_invented(self):
        stub = StubTransport()
        with self.assertRaises(LLMError):
            client_with(stub).complete_text(system="s", messages=[])
        self.assertEqual(stub.calls, [])

    def test_the_output_ceiling_can_be_overridden_per_call(self):
        stub = StubTransport()
        client_with(stub).complete_text(system="s", messages=USER, max_tokens=42)
        self.assertEqual(stub.calls[0]["config"].max_output_tokens, 42)

    def test_the_configured_ceiling_is_the_default(self):
        stub = StubTransport()
        client_with(stub).complete_text(system="s", messages=USER)
        self.assertEqual(stub.calls[0]["config"].max_output_tokens, 100)


class TestTextGeneration(unittest.TestCase):
    def test_text_completion_returns_the_text(self):
        stub = StubTransport(response_with("Hello there."))
        self.assertEqual(
            client_with(stub).complete_text(system="s", messages=USER), "Hello there."
        )

    def test_surrounding_whitespace_is_trimmed(self):
        stub = StubTransport(response_with("  spaced out \n"))
        self.assertEqual(
            client_with(stub).complete_text(system="s", messages=USER), "spaced out"
        )

    def test_an_empty_completion_is_an_error_not_an_empty_reply(self):
        """Returning '' would persist an empty assistant turn into the history."""
        stub = StubTransport(response_with(None, finish_reason="MAX_TOKENS"))
        with self.assertRaises(LLMError):
            client_with(stub).complete_text(system="s", messages=USER)

    def test_a_blocked_response_is_an_error(self):
        stub = StubTransport(types.GenerateContentResponse(candidates=[]))
        with self.assertRaises(LLMError):
            client_with(stub).complete_text(system="s", messages=USER)


class TestStructuredGeneration(unittest.TestCase):
    def test_json_completion_parses_the_response(self):
        stub = StubTransport(response_with('{"intent": "discovery"}'))
        result = client_with(stub).complete_json(system="s", messages=USER, schema={})
        self.assertEqual(result, {"intent": "discovery"})

    def test_the_request_asks_for_json_against_the_schema(self):
        stub = StubTransport(response_with("{}"))
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        client_with(stub).complete_json(system="s", messages=USER, schema=schema)
        config = stub.calls[0]["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertEqual(config.response_json_schema, to_response_schema(schema))

    def test_the_real_extraction_schema_is_accepted_by_the_sdk(self):
        """The SDK validates config on construction, so this catches a bad rewrite."""
        stub = StubTransport(response_with("{}"))
        client_with(stub).complete_json(
            system="s", messages=USER, schema=turn_analysis_schema(KB)
        )
        sent = stub.calls[0]["config"].response_json_schema
        self.assertEqual(list(sent["properties"]), ["intent", "is_hypothetical", "careers_mentioned", "profile_updates"])

    def test_unparseable_json_becomes_an_llm_error(self):
        stub = StubTransport(response_with('{"intent": "disc'))
        with self.assertRaises(LLMError):
            client_with(stub).complete_json(system="s", messages=USER, schema={})

    def test_an_empty_json_response_becomes_an_llm_error(self):
        stub = StubTransport(response_with(None, finish_reason="MAX_TOKENS"))
        with self.assertRaises(LLMError):
            client_with(stub).complete_json(system="s", messages=USER, schema={})


# --------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------

class TestFailuresAreAnonymised(unittest.TestCase):
    """Every provider failure becomes an LLMError carrying nothing revealing."""

    def _raise(self, error):
        stub = StubTransport(raises=error)
        with self.assertRaises(LLMError) as caught:
            client_with(stub).complete_text(system="s", messages=USER)
        message = str(caught.exception).lower()
        for leak in ("aiza", "secret", "api-key", "x-goog", "traceback"):
            self.assertNotIn(leak, message)
        return caught.exception

    def test_an_invalid_key_is_reported_as_an_auth_failure(self):
        error = self._raise(
            api_error(errors.ClientError, 401, "API key not valid: AIzaSyFAKESECRET")
        )
        self.assertEqual(str(error), "llm authentication failed")

    def test_a_forbidden_key_is_reported_as_an_auth_failure(self):
        self.assertEqual(
            str(self._raise(api_error(errors.ClientError, 403))), "llm authentication failed"
        )

    def test_quota_exhaustion_is_reported_as_a_rate_limit(self):
        """The free tier's daily cap arrives as a 429 like any other."""
        self.assertEqual(
            str(self._raise(api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"))),
            "llm rate limited",
        )

    def test_other_client_errors_are_generic(self):
        self.assertEqual(
            str(self._raise(api_error(errors.ClientError, 400, "bad schema"))),
            "llm request failed",
        )

    def test_server_errors_are_generic(self):
        self.assertEqual(
            str(self._raise(api_error(errors.ServerError, 503, "high demand"))),
            "llm request failed",
        )

    def test_transport_failures_are_caught(self):
        """Timeouts and dropped connections surface as httpx errors, not SDK ones."""
        import httpx

        self.assertEqual(
            str(self._raise(httpx.ReadTimeout("timed out"))), "llm call failed"
        )
        self.assertEqual(
            str(self._raise(httpx.ConnectError("dns"))), "llm call failed"
        )

    def test_an_unexpected_error_still_degrades_to_a_fallback(self):
        """An interactive turn must never become a 500 with a stack trace in it."""
        self.assertEqual(str(self._raise(RuntimeError("something odd"))), "llm call failed")

    def test_the_original_error_is_kept_for_the_log_only(self):
        original = api_error(errors.ClientError, 429, "quota for model gemini-x")
        stub = StubTransport(raises=original)
        with self.assertRaises(LLMError) as caught:
            client_with(stub).complete_text(system="s", messages=USER)
        self.assertIs(caught.exception.__cause__, original)


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------

class TestConstruction(unittest.TestCase):
    def _settings(self, **overrides) -> Settings:
        values = {"database_url": "postgresql+psycopg://u:p@localhost/db"}
        values.update(overrides)
        return Settings(**values)

    def test_no_key_means_no_client(self):
        self.assertIsNone(build_llm_client(self._settings(llm_api_key=None)))

    def test_a_blank_key_means_no_client(self):
        self.assertIsNone(build_llm_client(self._settings(llm_api_key="   ")))

    def test_a_configured_key_builds_a_client(self):
        client = build_llm_client(self._settings(llm_api_key="test-key"))
        self.assertIsInstance(client, GeminiClient)

    def test_construction_makes_no_network_call(self):
        """A bogus key must not stop the application starting."""
        self.assertIsInstance(
            build_llm_client(self._settings(llm_api_key="not-a-real-key")), GeminiClient
        )

    def test_the_model_comes_from_configuration(self):
        client = build_llm_client(
            self._settings(llm_api_key="test-key", llm_model="some-model-id")
        )
        self.assertEqual(client._model, "some-model-id")

    def test_the_timeout_is_converted_to_the_sdk_unit(self):
        """The setting is in seconds because that is what a human writes in .env."""
        client = build_llm_client(
            self._settings(llm_api_key="test-key", llm_timeout_seconds=30)
        )
        self.assertEqual(client._client._api_client._http_options.timeout, 30_000)

    def test_no_model_id_is_hardcoded_outside_configuration(self):
        """The model is deployment configuration, so only config may name one."""
        import app.conversation.service as service
        import app.llm.client as client
        import app.llm.prompts as prompt_module

        default = self._settings().llm_model
        self.assertTrue(default)
        for module in (client, prompt_module, service):
            source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn(default, source, module.__name__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
