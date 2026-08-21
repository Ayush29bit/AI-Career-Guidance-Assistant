"""The LLM layer.

Four modules, one job each:

* `schemas`  -- what the model is allowed to return, and how it is validated
* `prompts`  -- what the model is told, including the deterministic results it
                must explain rather than invent
* `client`   -- the provider-agnostic protocol every other module depends on
* `gemini`   -- the one implementation, and the only file that imports an SDK

Nothing in this package scores, ranks or calculates. Nothing outside it talks
to a provider SDK, and only `gemini` inside it does.
"""

from app.llm.client import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMUnavailableError,
    build_llm_client,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMMessage",
    "LLMUnavailableError",
    "build_llm_client",
]
