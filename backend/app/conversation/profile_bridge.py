"""Convert a stored student profile into what the recommendation engine expects.

This adapter is the single seam between the two halves of the system:

    PostgreSQL rows  ->  profile_bridge  ->  StudentProfile  ->  engine

It lives outside `app.database` on purpose. The database package knows nothing
about scoring, and the recommendation engine knows nothing about SQLAlchemy;
this module is the only place that knows both, so neither has to change to
accommodate the other.

Two rules drive everything below.

**Preserve the engine's semantics exactly.** A profile that came from the
database must score identically to the same profile built by hand. In
particular, an unknown experience level stays `None` -- the engine has a defined
neutral value for that (0.5, neither credit nor penalty) and inventing
"beginner" would quietly change every career score.

**Never infer.** A skill with no row is absent, not zero-with-confidence. A
missing tag is missing. Nothing here fills a gap with a plausible guess.

Everything goes through `build_profile`, which validates against the knowledge
base and raises `InvalidProfileError` listing every problem. So a profile that
drifted out of step with the taxonomy fails loudly here rather than producing a
quietly wrong ranking downstream.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.database import models
from app.knowledge.loader import KnowledgeBase
from app.recommendation.profile import StudentProfile, build_profile

#: Values of `student_profile_tags.tag_type`.
INTEREST_TAG = "interest"
WORK_PREFERENCE_TAG = "work_preference"

#: Values of `student_profile_entries.entry_type`.
STRENGTH_ENTRY = "strength"
DISLIKE_ENTRY = "dislike"
GOAL_ENTRY = "goal"


@dataclass(frozen=True)
class BridgedProfile:
    """Everything stored about a student, split by what the engine can use.

    `profile` is the engine's input and contains only what it scores. Strengths
    and dislikes are real information the conversation layer collects, but the
    engine has no term for them -- there is no controlled vocabulary behind them
    -- so they are carried alongside for the LLM and the UI rather than being
    forced into a scoring input they would distort.
    """

    profile: StudentProfile
    strengths: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()

    @property
    def goals(self) -> tuple[str, ...]:
        """Goals live on the engine profile itself, which already carries them."""
        return self.profile.goals


def _entry_sort_key(entry: models.StudentProfileEntry) -> tuple[bool, int]:
    """Order entries by insertion.

    Rows read back from the database have an increasing id. Objects built in
    memory and not yet flushed have `id is None`; those sort last, and Python's
    stable sort keeps them in the order they were added.
    """
    return (entry.id is None, entry.id or 0)


def _entries_of_type(db_profile: models.StudentProfile, entry_type: str) -> tuple[str, ...]:
    return tuple(
        entry.content
        for entry in sorted(db_profile.entries, key=_entry_sort_key)
        if entry.entry_type == entry_type
    )


def _tags_of_type(db_profile: models.StudentProfile, tag_type: str) -> tuple[str, ...]:
    # Sorted so the same stored profile always produces the same tuple. Tag
    # order does not affect scoring -- the engine compares sets -- but a stable
    # order keeps API responses and test assertions predictable.
    return tuple(
        sorted(tag.tag for tag in db_profile.tags if tag.tag_type == tag_type)
    )


def to_student_profile(
    db_profile: models.StudentProfile, kb: KnowledgeBase
) -> StudentProfile:
    """Build the engine's `StudentProfile` from a stored profile.

    Raises `InvalidProfileError` if anything stored is not part of the current
    knowledge base vocabularies.
    """
    return build_profile(
        kb,
        skills={
            # sorted for a deterministic dict order; proficiency is stored as a
            # float already, and float() keeps the type explicit either way
            skill.skill_id: float(skill.proficiency)
            for skill in sorted(db_profile.skills, key=lambda s: s.skill_id)
        },
        interests=_tags_of_type(db_profile, INTEREST_TAG),
        work_preferences=_tags_of_type(db_profile, WORK_PREFERENCE_TAG),
        # None stays None. Unknown experience is a real state, never guessed.
        experience_level=db_profile.experience_level,
        goals=_entries_of_type(db_profile, GOAL_ENTRY),
    )


def bridge_profile(db_profile: models.StudentProfile, kb: KnowledgeBase) -> BridgedProfile:
    """Convert a stored profile, keeping the facts the engine cannot score."""
    return BridgedProfile(
        profile=to_student_profile(db_profile, kb),
        strengths=_entries_of_type(db_profile, STRENGTH_ENTRY),
        dislikes=_entries_of_type(db_profile, DISLIKE_ENTRY),
    )
