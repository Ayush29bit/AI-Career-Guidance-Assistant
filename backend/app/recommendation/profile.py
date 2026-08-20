"""The structured student profile.

This is what the conversation layer produces and the recommendation engine
consumes. It holds only what the engine can actually use, expressed entirely in
the vocabularies already defined by the knowledge base -- canonical skill ids,
interest tags, work tags and experience levels. Nothing here invents a second
taxonomy.

Validation follows the same shape as the knowledge base loader: collect every
problem, then raise once, so a bad LLM extraction produces one useful error
rather than a cascade.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.knowledge.loader import KnowledgeBase


class InvalidProfileError(Exception):
    """Raised when a profile does not fit the knowledge base vocabularies."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        joined = "\n  - ".join(problems)
        super().__init__(f"{len(problems)} profile problem(s):\n  - {joined}")


@dataclass(frozen=True)
class StudentProfile:
    """What the application currently knows about the student.

    skills            canonical skill id -> proficiency/confidence in 0..1.
                      A skill that is absent means "not known to us", which the
                      engine treats as proficiency 0.
    interests         controlled interest tags (careers.yaml interest_tags)
    work_preferences  controlled work-style tags (careers.yaml work_tags)
    experience_level  beginner | intermediate | advanced, or None if unknown.
                      None is a real state and is never guessed at.
    goals             free text the student stated about what they want. Kept for
                      the LLM to use when explaining; deliberately NOT scored,
                      because there is no controlled vocabulary behind it.
    """

    skills: dict[str, float] = field(default_factory=dict)
    interests: tuple[str, ...] = ()
    work_preferences: tuple[str, ...] = ()
    experience_level: str | None = None
    goals: tuple[str, ...] = ()

    def proficiency(self, skill_id: str) -> float:
        """Proficiency in a skill; unknown skills are 0, never guessed."""
        return self.skills.get(skill_id, 0.0)

    def knows(self, skill_id: str) -> bool:
        return skill_id in self.skills

    @property
    def known_signal_count(self) -> int:
        """How many distinct pieces of information we hold. Used for cold start."""
        return (
            len(self.skills)
            + len(self.interests)
            + len(self.work_preferences)
            + (1 if self.experience_level else 0)
        )


def validate_profile(profile: StudentProfile, kb: KnowledgeBase) -> list[str]:
    """Return every way the profile fails to match the knowledge base vocabularies."""
    problems: list[str] = []

    for skill_id, proficiency in profile.skills.items():
        if skill_id not in kb.skills:
            problems.append(f"unknown skill id {skill_id!r}")
        if isinstance(proficiency, bool) or not isinstance(proficiency, (int, float)):
            problems.append(f"skill {skill_id!r}: proficiency {proficiency!r} is not a number")
        elif not 0.0 <= float(proficiency) <= 1.0:
            problems.append(f"skill {skill_id!r}: proficiency {proficiency!r} not in 0..1")

    for tag in profile.interests:
        if tag not in kb.interest_tags:
            problems.append(
                f"unknown interest tag {tag!r} (allowed: {', '.join(kb.interest_tags)})"
            )
    if len(set(profile.interests)) != len(profile.interests):
        problems.append("duplicate interest tags")

    for tag in profile.work_preferences:
        if tag not in kb.work_tags:
            problems.append(
                f"unknown work preference tag {tag!r} (allowed: {', '.join(kb.work_tags)})"
            )
    if len(set(profile.work_preferences)) != len(profile.work_preferences):
        problems.append("duplicate work preference tags")

    if profile.experience_level is not None and profile.experience_level not in kb.experience_levels:
        problems.append(
            f"unknown experience level {profile.experience_level!r} "
            f"(allowed: {', '.join(kb.experience_levels)})"
        )

    return problems


def build_profile(
    kb: KnowledgeBase,
    *,
    skills: dict[str, float] | None = None,
    interests: tuple[str, ...] | list[str] = (),
    work_preferences: tuple[str, ...] | list[str] = (),
    experience_level: str | None = None,
    goals: tuple[str, ...] | list[str] = (),
) -> StudentProfile:
    """Build a profile and validate it against the knowledge base.

    Raises InvalidProfileError listing every problem. This is the only supported
    way to create a profile from untrusted input (an LLM extraction, an API
    request), so nothing unvalidated reaches the engine.
    """
    profile = StudentProfile(
        skills=dict(skills or {}),
        interests=tuple(interests),
        work_preferences=tuple(work_preferences),
        experience_level=experience_level,
        goals=tuple(goals),
    )
    problems = validate_profile(profile, kb)
    if problems:
        raise InvalidProfileError(problems)
    return profile
