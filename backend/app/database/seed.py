"""Seed the PostgreSQL knowledge tables from the existing sources.

    cd backend
    python -m app.database.seed            # seed (or re-seed) then verify
    python -m app.database.seed --verify   # verify only, write nothing

Sources of truth are unchanged and are not duplicated here:

    skills.yaml / careers.yaml  ->  load_knowledge_base()
    coursera_course_dataset_v3.csv -> ingest()

Not one skill, career or course is written out in Python. This module only moves
what those two loaders already produce into tables. The raw CSV is opened
read-only.

**Idempotency.** Re-running is safe and converges: parents are upserted by
primary key and rows no longer present in the source are deleted, while the join
tables are rebuilt wholesale. The whole thing runs in one transaction, so a
failure leaves the previous contents intact.

The ordering below is not arbitrary. Join rows are cleared *before* parents are
deleted, because a skill removed from the taxonomy is still referenced by
skill_prerequisites until those rows are gone.

A skill that a student profile still claims is protected by an ON DELETE RESTRICT
foreign key, so removing it from skills.yaml makes the seed fail loudly rather
than quietly discarding what a student told us.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "backend") not in sys.path:  # allow `python app/database/seed.py` too
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import models  # noqa: E402
from app.database.session import session_scope  # noqa: E402
from app.ingestion.coursera import IngestionResult, dataset_vocabulary, ingest, read_rows  # noqa: E402
from app.knowledge.loader import (  # noqa: E402
    KnowledgeBase,
    KnowledgeBaseError,
    load_knowledge_base,
    validate_against_dataset,
)

DATASET = REPO_ROOT / "data" / "coursera_course_dataset_v3.csv"

#: Join tables, in the order they are cleared. Nothing references them, so
#: rebuilding them wholesale is the simplest correct way to stay idempotent.
_JOIN_TABLES = (
    models.CourseSkill,
    models.CareerSkill,
    models.CareerTag,
    models.SkillRelation,
    models.SkillPrerequisite,
)


class SeedError(Exception):
    """Raised when the sources or the seeded result do not check out."""


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def assert_vocabularies_agree(kb: KnowledgeBase) -> None:
    """Check the CHECK-constraint vocabularies still match the YAML enums.

    models.py has to repeat these as literals, because a migration cannot depend
    on parsing YAML. This is what stops the two drifting apart silently: add a
    skill kind to skills.yaml without a migration and the next seed says so.
    """
    problems: list[str] = []
    for label, from_yaml, from_models in (
        ("skill kinds", kb.kinds, models.SKILL_KINDS),
        ("experience levels", kb.experience_levels, models.EXPERIENCE_LEVELS),
    ):
        if set(from_yaml) != set(from_models):
            problems.append(
                f"{label} differ: yaml={sorted(from_yaml)} models={sorted(from_models)}"
            )
    if problems:
        raise SeedError(
            "knowledge base vocabularies no longer match the database CHECK "
            "constraints; a migration is needed:\n  - " + "\n  - ".join(problems)
        )


def load_sources(dataset: Path) -> tuple[KnowledgeBase, IngestionResult]:
    """Load and fully validate both sources before touching the database."""
    if not dataset.exists():
        raise SeedError(f"dataset not found: {dataset}")

    try:
        kb = load_knowledge_base()
    except KnowledgeBaseError as error:
        raise SeedError(f"knowledge base validation failed:\n{error}") from error

    assert_vocabularies_agree(kb)

    # The same alias/out-of-scope partition check the ingestion CLI runs. Seeding
    # a catalogue built from a stale alias map would quietly lose courses.
    alias_problems = validate_against_dataset(kb, dataset_vocabulary(read_rows(dataset)))
    if alias_problems:
        raise SeedError(
            "alias validation failed against the dataset:\n  - " + "\n  - ".join(alias_problems)
        )

    return kb, ingest(dataset, kb)


# --------------------------------------------------------------------------
# row builders -- each one is a straight projection of an already-loaded object
# --------------------------------------------------------------------------

def _skill_rows(kb: KnowledgeBase) -> list[dict]:
    return [
        {"id": s.id, "name": s.name, "category": s.category, "kind": s.kind}
        for s in kb.skills.values()
    ]


def _skill_prerequisite_rows(kb: KnowledgeBase) -> list[dict]:
    return [
        {"skill_id": s.id, "prerequisite_skill_id": prerequisite}
        for s in kb.skills.values()
        for prerequisite in s.prerequisites
    ]


def _skill_relation_rows(kb: KnowledgeBase) -> list[dict]:
    return [
        {"skill_id": s.id, "related_skill_id": related}
        for s in kb.skills.values()
        for related in s.related
    ]


def _career_rows(kb: KnowledgeBase) -> list[dict]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "short_description": c.short_description,
            "expected_experience": c.expected_experience,
        }
        for c in kb.careers.values()
    ]


def _career_tag_rows(kb: KnowledgeBase) -> list[dict]:
    rows: list[dict] = []
    for career in kb.careers.values():
        rows.extend(
            {"career_id": career.id, "tag_type": "interest", "tag": tag}
            for tag in career.interest_tags
        )
        rows.extend(
            {"career_id": career.id, "tag_type": "work_preference", "tag": tag}
            for tag in career.work_tags
        )
    return rows


def _career_skill_rows(kb: KnowledgeBase) -> list[dict]:
    return [
        {
            "career_id": career.id,
            "skill_id": requirement.skill,
            "importance": requirement.importance,
            "required_level": requirement.required_level,
        }
        for career in kb.careers.values()
        for requirement in career.required_skills
    ]


def _course_rows(result: IngestionResult) -> list[dict]:
    return [
        {
            "id": c.course_id,
            "title": c.title,
            "organization": c.organization,
            "url": c.url,
            "rating": c.rating,
            "review_count": c.review_count,
            "review_count_raw": c.review_count_raw or None,
            "review_count_is_approximate": c.review_count_is_approximate,
            "students_enrolled": c.students_enrolled,
            "difficulty": c.difficulty,
            "course_type": c.course_type,
            "duration": c.duration,
            "description": c.description,
        }
        for c in result.courses
    ]


def _course_skill_rows(result: IngestionResult) -> list[dict]:
    # course.skills is already canonical and de-duplicated by ingestion. Human
    # skills are included; consumers filter on skills.kind.
    return [
        {"course_id": course.course_id, "skill_id": skill_id}
        for course in result.courses
        for skill_id in course.skills
    ]


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def _upsert_by_id(session: Session, model, rows: list[dict], update_columns: tuple[str, ...]) -> None:
    """Insert rows, updating the listed columns when the primary key already exists.

    created_at survives a re-seed; updated_at is refreshed so it reflects the
    last time the row was written.
    """
    if not rows:
        return
    statement = pg_insert(model)
    statement = statement.on_conflict_do_update(
        index_elements=[model.id],
        set_={
            **{column: statement.excluded[column] for column in update_columns},
            "updated_at": func.now(),
        },
    )
    session.execute(statement, rows)


def _delete_missing(session: Session, model, keep_ids: set[str]) -> int:
    """Remove rows whose id is no longer produced by the source."""
    statement = delete(model)
    if keep_ids:
        statement = statement.where(model.id.notin_(keep_ids))
    result = session.execute(statement)
    return result.rowcount or 0


def seed(session: Session, kb: KnowledgeBase, result: IngestionResult) -> dict[str, int]:
    """Write both sources into the database. Caller owns the transaction."""
    # 1. Clear the join tables first: a parent that disappeared from the source
    #    cannot be deleted while a join row still points at it.
    for model in _JOIN_TABLES:
        session.execute(delete(model))

    # 2. Parents.
    skill_rows = _skill_rows(kb)
    _upsert_by_id(session, models.Skill, skill_rows, ("name", "category", "kind"))
    removed_skills = _delete_missing(session, models.Skill, {r["id"] for r in skill_rows})

    career_rows = _career_rows(kb)
    _upsert_by_id(
        session,
        models.Career,
        career_rows,
        ("name", "short_description", "expected_experience"),
    )
    removed_careers = _delete_missing(session, models.Career, {r["id"] for r in career_rows})

    course_rows = _course_rows(result)
    _upsert_by_id(
        session,
        models.Course,
        course_rows,
        (
            "title",
            "organization",
            "url",
            "rating",
            "review_count",
            "review_count_raw",
            "review_count_is_approximate",
            "students_enrolled",
            "difficulty",
            "course_type",
            "duration",
            "description",
        ),
    )
    removed_courses = _delete_missing(session, models.Course, {r["id"] for r in course_rows})

    # 3. Rebuild the join tables now that every parent exists.
    for model, rows in (
        (models.SkillPrerequisite, _skill_prerequisite_rows(kb)),
        (models.SkillRelation, _skill_relation_rows(kb)),
        (models.CareerTag, _career_tag_rows(kb)),
        (models.CareerSkill, _career_skill_rows(kb)),
        (models.CourseSkill, _course_skill_rows(result)),
    ):
        if rows:
            session.execute(pg_insert(model), rows)

    return {
        "removed_skills": removed_skills,
        "removed_careers": removed_careers,
        "removed_courses": removed_courses,
    }


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

#: Every table the seed owns or the application writes, for the count report.
COUNTED_MODELS = (
    models.Skill,
    models.SkillPrerequisite,
    models.SkillRelation,
    models.Career,
    models.CareerTag,
    models.CareerSkill,
    models.Course,
    models.CourseSkill,
    models.StudentProfile,
    models.StudentProfileSkill,
    models.StudentProfileTag,
    models.StudentProfileEntry,
    models.Conversation,
    models.Message,
)


def row_counts(session: Session) -> dict[str, int]:
    return {
        model.__tablename__: session.execute(
            select(func.count()).select_from(model)
        ).scalar_one()
        for model in COUNTED_MODELS
    }


def verify(session: Session, kb: KnowledgeBase, result: IngestionResult) -> list[str]:
    """Compare what is stored against what the sources produce, right now.

    Foreign keys and primary keys already make orphans and duplicates
    impossible, so this checks the thing constraints cannot: that the stored
    contents are exactly what the current sources describe.
    """
    problems: list[str] = []
    counts = row_counts(session)

    expected = {
        "skills": len(_skill_rows(kb)),
        "skill_prerequisites": len(_skill_prerequisite_rows(kb)),
        "skill_relations": len(_skill_relation_rows(kb)),
        "careers": len(_career_rows(kb)),
        "career_tags": len(_career_tag_rows(kb)),
        "career_skills": len(_career_skill_rows(kb)),
        "courses": len(_course_rows(result)),
        "course_skills": len(_course_skill_rows(result)),
    }
    for table, want in expected.items():
        got = counts[table]
        if got != want:
            problems.append(f"{table}: {got} rows in the database, {want} in the source")

    # Identity, not just cardinality: the right number of the wrong rows is
    # still wrong.
    for model, source_ids, label in (
        (models.Skill, set(kb.skills), "skills"),
        (models.Career, set(kb.careers), "careers"),
        (models.Course, {c.course_id for c in result.courses}, "courses"),
    ):
        stored = set(session.execute(select(model.id)).scalars())
        for missing in sorted(source_ids - stored):
            problems.append(f"{label}: {missing!r} is in the source but not stored")
        for extra in sorted(stored - source_ids):
            problems.append(f"{label}: {extra!r} is stored but not in the source")

    return problems


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_counts(counts: dict[str, int]) -> None:
    width = max(len(name) for name in counts)
    for name, count in counts.items():
        print(f"  {name.ljust(width)}  {count:>6}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the knowledge tables into PostgreSQL.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument(
        "--verify", action="store_true", help="verify the stored data only, write nothing"
    )
    args = parser.parse_args(argv)

    try:
        kb, result = load_sources(args.dataset)
    except SeedError as error:
        print(f"Seed aborted.\n{error}", file=sys.stderr)
        return 1

    print("Sources loaded")
    print(f"  skills   {len(kb.skills)}")
    print(f"  careers  {len(kb.careers)}")
    print(f"  courses  {len(result.courses)} (from {args.dataset.name})")

    with session_scope() as session:
        if not args.verify:
            removed = seed(session, kb, result)
            session.flush()
            print("\nSeeded")
            if any(removed.values()):
                for label, count in removed.items():
                    if count:
                        print(f"  {label}: {count}")

        print("\nRow counts")
        _print_counts(row_counts(session))

        problems = verify(session, kb, result)
        if problems:
            print("\nVerification FAILED", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print("\nVerification passed: stored data matches the sources exactly.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
