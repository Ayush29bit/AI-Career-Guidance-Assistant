"""What the LLM is allowed to return, and how it is validated.

Everything the LLM produces for *code* to consume passes through this module.
Two layers of checking happen here, and they are deliberately different:

1. **Shape** -- Pydantic. Types, ranges, enum membership. A payload that fails
   this is not repairable, so the caller treats the whole extraction as failed
   and keeps the previous profile.

2. **Vocabulary** -- `filter_to_vocabulary`. Skill ids, interest tags and work
   tags must exist in the knowledge base. A value that does not is *dropped*
   and reported, not raised: one hallucinated skill id must not throw away the
   four good facts that came with it.

The distinction matters. A malformed response means "we learned nothing this
turn". An invented skill id means "we learned four things and ignored a fifth".

Nothing here computes anything. There is no field for a career score, a course,
a skill gap or a ranking, so the LLM has no place to put one even if it tried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.knowledge.loader import KnowledgeBase

#: How a fact reached the profile.
#:
#: explicit  the student said it -- "I've used Python for two years"
#: inferred  we concluded it -- "I built three ML projects" implying Python
#:
#: The engine scores both the same; the merge rules do not. An inferred fact
#: never overwrites an explicit one (app.conversation.merge).
Source = Literal["explicit", "inferred"]

#: Lightweight routing labels. A flat closed set on purpose -- they choose
#: which deterministic result to fetch, nothing more. There is no state machine
#: behind them, and no intent lets the model skip the engine.
Intent = Literal[
    "discovery",
    "profile_update",
    "career_recommendation",
    "why_recommendation",
    "career_comparison",
    "skill_gap",
    "course_recommendation",
    "roadmap",
    "what_if",
    "general_conversation",
]

INTENTS: tuple[str, ...] = (
    "discovery",
    "profile_update",
    "career_recommendation",
    "why_recommendation",
    "career_comparison",
    "skill_gap",
    "course_recommendation",
    "roadmap",
    "what_if",
    "general_conversation",
)

#: Intents whose answer must be grounded in engine output rather than in
#: whatever the model happens to know about a job title.
ENGINE_BACKED_INTENTS: frozenset[str] = frozenset(
    {
        "career_recommendation",
        "why_recommendation",
        "career_comparison",
        "skill_gap",
        "course_recommendation",
        "roadmap",
        "what_if",
    }
)

#: Intents that ask for courses or a learning sequence, so the course catalogue
#: has to be consulted. Kept separate because loading it is the one genuinely
#: expensive thing the turn can do.
COURSE_BACKED_INTENTS: frozenset[str] = frozenset({"course_recommendation", "roadmap"})


# --------------------------------------------------------------------------
# the structured extraction
# --------------------------------------------------------------------------

class ExtractedSkill(BaseModel):
    """One skill, by canonical id. Proficiency is the student's level, not a score."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    proficiency: float = Field(ge=0.0, le=1.0)
    source: Source


class ExtractedTag(BaseModel):
    """One interest or work-preference tag, from the careers.yaml vocabularies."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    source: Source


class ExtractedEntry(BaseModel):
    """A free-text strength, dislike or goal.

    No controlled vocabulary exists for these, which is exactly why they are
    never scored. The length cap is a guard against the model writing an essay
    into a field meant to hold a phrase.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=200)
    source: Source

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("entry content cannot be blank")
        return stripped


class ExtractedExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["beginner", "intermediate", "advanced"]
    source: Source


class ProfileExtraction(BaseModel):
    """Everything the LLM believes this turn added to what we know.

    Deltas only. The model is never asked to restate the whole profile, so it
    cannot silently drop a fact by omitting it.
    """

    model_config = ConfigDict(extra="forbid")

    skills: list[ExtractedSkill] = Field(default_factory=list, max_length=30)
    interests: list[ExtractedTag] = Field(default_factory=list, max_length=15)
    work_preferences: list[ExtractedTag] = Field(default_factory=list, max_length=15)
    experience: ExtractedExperience | None = None
    strengths: list[ExtractedEntry] = Field(default_factory=list, max_length=10)
    dislikes: list[ExtractedEntry] = Field(default_factory=list, max_length=10)
    goals: list[ExtractedEntry] = Field(default_factory=list, max_length=10)

    #: Facts the student took back, either for real ("actually, I don't enjoy
    #: statistics") or hypothetically ("what if I didn't?"). A tag has no
    #: negative value to record, so withdrawing one has to be its own list.
    #: There is no `source` here: a removal is always something the student
    #: said, never something we inferred.
    removed_interests: list[str] = Field(default_factory=list, max_length=10)
    removed_work_preferences: list[str] = Field(default_factory=list, max_length=10)
    removed_skills: list[str] = Field(default_factory=list, max_length=10)

    @property
    def is_empty(self) -> bool:
        return not (
            self.skills
            or self.interests
            or self.work_preferences
            or self.experience
            or self.strengths
            or self.dislikes
            or self.goals
            or self.has_removals
        )

    @property
    def has_removals(self) -> bool:
        return bool(
            self.removed_interests or self.removed_work_preferences or self.removed_skills
        )


class TurnAnalysis(BaseModel):
    """The whole structured half of one turn: what the student wants, and what they told us.

    `careers_mentioned` carries career ids the student named, so "compare those
    two" can be answered against the right rows. It is a lookup key, never a
    recommendation -- ranking stays with the engine.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    profile_updates: ProfileExtraction = Field(default_factory=ProfileExtraction)
    careers_mentioned: list[str] = Field(default_factory=list, max_length=5)
    #: True when the student is exploring a hypothetical ("what if I disliked
    #: statistics"). Hypothetical updates are scored but never persisted.
    is_hypothetical: bool = False


# --------------------------------------------------------------------------
# vocabulary filtering
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FilteredAnalysis:
    """A turn analysis with every out-of-vocabulary value removed.

    `rejected` is kept rather than discarded: a model that keeps inventing
    `pytorch` when the taxonomy calls it `deep_learning` is a prompt problem,
    and the only way to see it is to log what was thrown away.
    """

    analysis: TurnAnalysis
    rejected: tuple[str, ...] = ()

    @property
    def had_rejections(self) -> bool:
        return bool(self.rejected)


@dataclass
class _Sink:
    """Collects what was dropped, so nothing is discarded silently."""

    rejected: list[str] = field(default_factory=list)

    def drop(self, what: str, value: str) -> None:
        self.rejected.append(f"{what}={value!r}")


def _keep_ids(values: list[str], allowed, label: str, sink: _Sink) -> list[str]:
    """Filter a plain id list (a removal list) to the allowed vocabulary."""
    kept: list[str] = []
    for value in values:
        if value not in allowed:
            sink.drop(f"removed_{label}", value)
            continue
        if value not in kept:
            kept.append(value)
    return kept


def _keep_tags(
    tags: list[ExtractedTag], allowed: tuple[str, ...], label: str, sink: _Sink
) -> list[ExtractedTag]:
    kept: list[ExtractedTag] = []
    seen: set[str] = set()
    for tag in tags:
        if tag.tag not in allowed:
            sink.drop(label, tag.tag)
            continue
        if tag.tag in seen:  # a duplicate is redundant, not an error
            continue
        seen.add(tag.tag)
        kept.append(tag)
    return kept


def filter_to_vocabulary(analysis: TurnAnalysis, kb: KnowledgeBase) -> FilteredAnalysis:
    """Drop every value the knowledge base does not define.

    This is what stops an invented skill id reaching the database and, from
    there, the engine. It never raises: the good facts in a partly-bad payload
    are still worth keeping.
    """
    sink = _Sink()
    updates = analysis.profile_updates

    kept_skills: list[ExtractedSkill] = []
    seen_skills: set[str] = set()
    for skill in updates.skills:
        if skill.skill_id not in kb.skills:
            sink.drop("skill_id", skill.skill_id)
            continue
        if skill.skill_id in seen_skills:
            continue
        seen_skills.add(skill.skill_id)
        kept_skills.append(skill)

    kept_careers: list[str] = []
    for career_id in analysis.careers_mentioned:
        if career_id not in kb.careers:
            sink.drop("career_id", career_id)
            continue
        if career_id not in kept_careers:
            kept_careers.append(career_id)

    cleaned = analysis.model_copy(
        update={
            "profile_updates": updates.model_copy(
                update={
                    "skills": kept_skills,
                    "interests": _keep_tags(
                        updates.interests, kb.interest_tags, "interest", sink
                    ),
                    "work_preferences": _keep_tags(
                        updates.work_preferences, kb.work_tags, "work_preference", sink
                    ),
                    "removed_interests": _keep_ids(
                        updates.removed_interests, kb.interest_tags, "interest", sink
                    ),
                    "removed_work_preferences": _keep_ids(
                        updates.removed_work_preferences,
                        kb.work_tags,
                        "work_preference",
                        sink,
                    ),
                    "removed_skills": _keep_ids(
                        updates.removed_skills, kb.skills, "skill", sink
                    ),
                }
            ),
            "careers_mentioned": kept_careers,
        }
    )
    return FilteredAnalysis(analysis=cleaned, rejected=tuple(sink.rejected))


def parse_turn_analysis(payload: object, kb: KnowledgeBase) -> FilteredAnalysis:
    """Validate a raw provider payload and strip anything outside the vocabularies.

    Raises `pydantic.ValidationError` when the shape itself is wrong -- the
    caller reads that as "this turn taught us nothing" and keeps the profile
    exactly as it was.
    """
    return filter_to_vocabulary(TurnAnalysis.model_validate(payload), kb)


# --------------------------------------------------------------------------
# the JSON schema handed to the provider
# --------------------------------------------------------------------------

_SOURCE_PROPERTY = {"type": "string", "enum": ["explicit", "inferred"]}


def _entry_list_schema(description: str) -> dict:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "source": _SOURCE_PROPERTY},
            "required": ["content", "source"],
            "additionalProperties": False,
        },
    }


def _tag_list_schema(description: str, allowed: tuple[str, ...]) -> dict:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "enum": list(allowed)},
                "source": _SOURCE_PROPERTY,
            },
            "required": ["tag", "source"],
            "additionalProperties": False,
        },
    }


def turn_analysis_schema(kb: KnowledgeBase) -> dict:
    """JSON schema for one turn analysis, with the vocabularies baked in as enums.

    Built from the knowledge base rather than written out by hand, so adding a
    skill to skills.yaml widens what the model may emit without anyone editing
    this file. The enums make most invalid values impossible at the provider;
    `filter_to_vocabulary` still runs afterwards, because a schema the provider
    enforces is not the same thing as a guarantee.
    """
    skill_ids = sorted(kb.skills)
    career_ids = sorted(kb.careers)
    return {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": list(INTENTS)},
            "is_hypothetical": {
                "type": "boolean",
                "description": (
                    "True only when the student is exploring a hypothetical "
                    "change rather than stating a fact about themselves."
                ),
            },
            "careers_mentioned": {
                "type": "array",
                "description": "Career ids the student referred to in this turn.",
                "items": {"type": "string", "enum": career_ids},
            },
            "profile_updates": {
                "type": "object",
                "description": "Only what THIS turn added. Never restate known facts.",
                "properties": {
                    "skills": {
                        "type": "array",
                        "description": (
                            "Skills evidenced by this turn. proficiency is the "
                            "student's own level in 0..1, never a career score."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "skill_id": {"type": "string", "enum": skill_ids},
                                "proficiency": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0,
                                },
                                "source": _SOURCE_PROPERTY,
                            },
                            "required": ["skill_id", "proficiency", "source"],
                            "additionalProperties": False,
                        },
                    },
                    "interests": _tag_list_schema(
                        "Subject areas the student is drawn to.", kb.interest_tags
                    ),
                    "work_preferences": _tag_list_schema(
                        "The kind of work the student enjoys.", kb.work_tags
                    ),
                    "experience": {
                        "type": ["object", "null"],
                        "description": (
                            "Overall experience level, only if stated or clearly "
                            "implied. null when unknown -- never guess."
                        ),
                        "properties": {
                            "level": {"type": "string", "enum": list(kb.experience_levels)},
                            "source": _SOURCE_PROPERTY,
                        },
                        "required": ["level", "source"],
                        "additionalProperties": False,
                    },
                    "strengths": _entry_list_schema(
                        "Short phrases; what the student is good at."
                    ),
                    "dislikes": _entry_list_schema(
                        "Short phrases; what the student dislikes."
                    ),
                    "goals": _entry_list_schema("Short phrases; what the student wants."),
                    "removed_interests": {
                        "type": "array",
                        "description": (
                            "Interest tags the student withdrew, really or "
                            "hypothetically. Only when they said so."
                        ),
                        "items": {"type": "string", "enum": list(kb.interest_tags)},
                    },
                    "removed_work_preferences": {
                        "type": "array",
                        "description": "Work preference tags the student withdrew.",
                        "items": {"type": "string", "enum": list(kb.work_tags)},
                    },
                    "removed_skills": {
                        "type": "array",
                        "description": (
                            "Skills the student no longer claims, or is asking "
                            "to set aside for a hypothetical."
                        ),
                        "items": {"type": "string", "enum": skill_ids},
                    },
                },
                "required": [
                    "skills",
                    "interests",
                    "work_preferences",
                    "experience",
                    "strengths",
                    "dislikes",
                    "goals",
                    "removed_interests",
                    "removed_work_preferences",
                    "removed_skills",
                ],
                "additionalProperties": False,
            },
        },
        "required": ["intent", "is_hypothetical", "careers_mentioned", "profile_updates"],
        "additionalProperties": False,
    }
