"""Conversation layer.

Four modules, in the order one turn travels through them:

* `profile_bridge` -- stored rows to the engine's `StudentProfile`
* `merge`          -- a validated extraction back into stored rows
* `context`        -- deterministic results into the briefing the LLM may quote
* `service`        -- the orchestration that runs one turn end to end

The LLM lives in `app.llm`; the scoring lives in `app.recommendation`. This
package is what connects them, and it is deliberately the only place that knows
about both.
"""

from app.conversation.profile_bridge import BridgedProfile, bridge_profile, to_student_profile
from app.conversation.service import TurnResult, handle_user_message

__all__ = [
    "BridgedProfile",
    "TurnResult",
    "bridge_profile",
    "handle_user_message",
    "to_student_profile",
]
