"""Merge a validated extraction into the stored student profile.

The input is already safe by the time it arrives: `app.llm.schemas` has checked
the shape and dropped every value outside the knowledge base vocabularies. What
is left for this module is the question that validation cannot answer -- when
new information disagrees with what we already hold, which one wins.

Three rules decide that, and they are the same for skills, tags, entries and
experience level:

1. **Explicit beats inferred.** If the student said it, an inference must not
   overwrite it. "I built an ML project" should never quietly downgrade
   "I've used Python for two years".
2. **Like replaces like.** Explicit over explicit, inferred over inferred: the
   newer statement wins, because the latest thing a student says about
   themselves is the most current thing we know.
3. **Deletion is explicit or it does not happen.** A merge adds and updates; it
   removes only what the extraction listed as withdrawn ("actually, I don't
   enjoy statistics"). Nothing disappears because a later turn failed to
   mention it.

Free-text entries follow the same spirit but compare on content: the same
strength phrased identically twice is one strength, not two.

Nothing here is scored, ranked or weighted. The engine reads the merged profile
afterwards and is unaffected by how any of it got there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database import models
from app.llm.schemas import ProfileExtraction

logger = logging.getLogger(__name__)

EXPLICIT = "explicit"
INFERRED = "inferred"

#: Which extraction field lands in which entry_type.
_ENTRY_FIELDS = (("strengths", "strength"), ("dislikes", "dislike"), ("goals", "goal"))

#: Which extraction field lands in which tag_type.
_TAG_FIELDS = (("interests", "interest"), ("work_preferences", "work_preference"))


@dataclass
class MergeReport:
    """What the merge actually changed. Used for logging and for tests.

    Kept as counts and ids rather than as before/after rows: the point is to be
    able to say "this turn added two skills and was not allowed to overwrite
    one", not to reconstruct the profile's history.
    """

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    #: Changes refused because an inference would have overwritten a statement.
    protected: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)

    def summary(self) -> str:
        return (
            f"added={len(self.added)} updated={len(self.updated)} "
            f"removed={len(self.removed)} protected={len(self.protected)}"
        )


def _may_overwrite(existing_source: str, new_source: str) -> bool:
    """Whether `new_source` is allowed to replace a fact recorded as `existing_source`.

    The single expression of rules 1 and 2. Everything below defers to it, so
    the policy is stated once.
    """
    return not (existing_source == EXPLICIT and new_source == INFERRED)


def merge_profile_extraction(
    session: Session, db_profile: models.StudentProfile, updates: ProfileExtraction
) -> MergeReport:
    """Apply an extraction to a stored profile, in the caller's transaction.

    Does not commit. The caller decides whether the turn as a whole should be
    persisted, which is what keeps a failed turn from leaving half a profile
    behind.
    """
    report = MergeReport()

    _merge_skills(session, db_profile, updates, report)
    _merge_tags(session, db_profile, updates, report)
    _merge_entries(session, db_profile, updates, report)
    _merge_experience(db_profile, updates, report)
    # Last, so a turn that both states and withdraws the same tag ends with the
    # withdrawal -- which is what "actually, no" means.
    _apply_removals(session, db_profile, updates, report)

    if report.protected:
        # Not an error: the rules working. Worth seeing when tuning the prompt,
        # because a model that keeps inferring over stated facts is a prompt bug.
        logger.info("profile merge protected explicit facts: %s", report.protected)

    return report


# --------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------

def _merge_skills(
    session: Session,
    db_profile: models.StudentProfile,
    updates: ProfileExtraction,
    report: MergeReport,
) -> None:
    existing = {row.skill_id: row for row in db_profile.skills}

    for extracted in updates.skills:
        current = existing.get(extracted.skill_id)
        if current is None:
            row = models.StudentProfileSkill(
                student_profile_id=db_profile.id,
                skill_id=extracted.skill_id,
                proficiency=extracted.proficiency,
                source=extracted.source,
            )
            session.add(row)
            db_profile.skills.append(row)
            existing[extracted.skill_id] = row
            report.added.append(f"skill:{extracted.skill_id}")
            continue

        if not _may_overwrite(current.source, extracted.source):
            report.protected.append(f"skill:{extracted.skill_id}")
            continue

        if (
            current.proficiency == extracted.proficiency
            and current.source == extracted.source
        ):
            continue  # nothing new; do not report a change that did not happen

        current.proficiency = extracted.proficiency
        current.source = extracted.source
        report.updated.append(f"skill:{extracted.skill_id}")


# --------------------------------------------------------------------------
# tags
# --------------------------------------------------------------------------

def _merge_tags(
    session: Session,
    db_profile: models.StudentProfile,
    updates: ProfileExtraction,
    report: MergeReport,
) -> None:
    existing = {(row.tag_type, row.tag): row for row in db_profile.tags}

    for field_name, tag_type in _TAG_FIELDS:
        for extracted in getattr(updates, field_name):
            key = (tag_type, extracted.tag)
            current = existing.get(key)
            if current is None:
                row = models.StudentProfileTag(
                    student_profile_id=db_profile.id,
                    tag_type=tag_type,
                    tag=extracted.tag,
                    source=extracted.source,
                )
                session.add(row)
                db_profile.tags.append(row)
                existing[key] = row
                report.added.append(f"{tag_type}:{extracted.tag}")
                continue

            # A tag is present or absent -- there is no value to update. The
            # only thing that can change is how confident we are it belongs.
            if current.source == extracted.source:
                continue
            if not _may_overwrite(current.source, extracted.source):
                report.protected.append(f"{tag_type}:{extracted.tag}")
                continue
            current.source = extracted.source
            report.updated.append(f"{tag_type}:{extracted.tag}")


# --------------------------------------------------------------------------
# free-text entries
# --------------------------------------------------------------------------

def _merge_entries(
    session: Session,
    db_profile: models.StudentProfile,
    updates: ProfileExtraction,
    report: MergeReport,
) -> None:
    existing = {
        (row.entry_type, row.content.strip().lower()): row for row in db_profile.entries
    }

    for field_name, entry_type in _ENTRY_FIELDS:
        for extracted in getattr(updates, field_name):
            key = (entry_type, extracted.content.strip().lower())
            current = existing.get(key)
            if current is None:
                row = models.StudentProfileEntry(
                    student_profile_id=db_profile.id,
                    entry_type=entry_type,
                    content=extracted.content,
                    source=extracted.source,
                )
                session.add(row)
                db_profile.entries.append(row)
                existing[key] = row
                report.added.append(f"{entry_type}:{extracted.content}")
                continue

            if current.source == extracted.source:
                continue
            if not _may_overwrite(current.source, extracted.source):
                report.protected.append(f"{entry_type}:{extracted.content}")
                continue
            current.source = extracted.source
            report.updated.append(f"{entry_type}:{extracted.content}")


# --------------------------------------------------------------------------
# removals
# --------------------------------------------------------------------------

def _apply_removals(
    session: Session,
    db_profile: models.StudentProfile,
    updates: ProfileExtraction,
    report: MergeReport,
) -> None:
    """Delete facts the student withdrew.

    Only what the extraction named. A removal is by definition something the
    student said, so it overrides an explicit fact -- the alternative is telling
    a student we know better than they do about their own dislikes.
    """
    if not updates.has_removals:
        return

    removed_tags = {
        ("interest", tag) for tag in updates.removed_interests
    } | {("work_preference", tag) for tag in updates.removed_work_preferences}

    for row in list(db_profile.tags):
        if (row.tag_type, row.tag) in removed_tags:
            db_profile.tags.remove(row)
            session.delete(row)
            report.removed.append(f"{row.tag_type}:{row.tag}")

    removed_skills = set(updates.removed_skills)
    for row in list(db_profile.skills):
        if row.skill_id in removed_skills:
            db_profile.skills.remove(row)
            session.delete(row)
            report.removed.append(f"skill:{row.skill_id}")


# --------------------------------------------------------------------------
# experience level
# --------------------------------------------------------------------------

def _merge_experience(
    db_profile: models.StudentProfile, updates: ProfileExtraction, report: MergeReport
) -> None:
    extracted = updates.experience
    if extracted is None:
        return  # silence is not "unknown"; it means this turn said nothing

    if db_profile.experience_level is None:
        db_profile.experience_level = extracted.level
        db_profile.experience_level_source = extracted.source
        report.added.append(f"experience:{extracted.level}")
        return

    # A stored level with no recorded source predates this column, so it was
    # written by something that stated it outright. Treat it as explicit.
    current_source = db_profile.experience_level_source or EXPLICIT
    if not _may_overwrite(current_source, extracted.source):
        report.protected.append(f"experience:{db_profile.experience_level}")
        return

    if (
        db_profile.experience_level == extracted.level
        and current_source == extracted.source
    ):
        return

    db_profile.experience_level = extracted.level
    db_profile.experience_level_source = extracted.source
    report.updated.append(f"experience:{extracted.level}")
