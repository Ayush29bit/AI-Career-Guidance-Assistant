"""Conversation layer.

For now this holds only the profile bridge: the adapter that turns stored
student profiles into the recommendation engine's input. The LLM-driven
conversation logic will join it here in a later phase.
"""

from app.conversation.profile_bridge import BridgedProfile, bridge_profile, to_student_profile

__all__ = ["BridgedProfile", "bridge_profile", "to_student_profile"]
