"""Read and normalize the official Coursera dataset.

Reads data/coursera_course_dataset_v3.csv (v3 only -- v2 is a strict subset that
carries no extra information) and produces a normalized in-memory representation
suitable for loading into PostgreSQL later. The raw CSV is never modified.

Field notes that come straight from the dataset audit:
  * "Extract, Transform, Load" is a single skill containing commas, unquoted
    inside the Skills field. It must be protected before splitting or 35 rows
    produce three phantom skills.
  * Every Skills field has a leading space; every token needs stripping.
  * The leading index column is unstable (0..628 with six gaps, and it differs
    between v2 and v3) so it is ignored entirely. Course ids are slugs.
  * Review Count is mixed: "411" is exact, "20K" and "8.1K" are approximations.
  * course_url, course_students_enrolled and course_description are missing on
    roughly a third of rows.
  * course_description is unreliable -- some rows carry another course's text.
    It is stored for display only and marked untrusted; no recommendation logic
    may read it.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.knowledge.loader import KnowledgeBase

# The one skill in the dataset whose name contains commas.
_COMMA_SKILL = "Extract, Transform, Load"
_COMMA_SKILL_PLACEHOLDER = "\x00ETL\x00"

EXPECTED_COLUMNS = [
    "",
    "Title",
    "Organization",
    "Skills",
    "Ratings",
    "course_url",
    "course_students_enrolled",
    "course_description",
    "Review Count",
    "Difficulty",
    "Type",
    "Duration",
]

DIFFICULTY_MAP = {
    "Beginner": "beginner",
    "Intermediate": "intermediate",
    "Advanced": "advanced",
    "Mixed": "mixed",
}

COURSE_TYPE_MAP = {
    "Course": "course",
    "Specialization": "specialization",
    "Professional Certificate": "professional_certificate",
    "Guided Project": "guided_project",
    "Project": "project",
}

DURATION_MAP = {
    "Less Than 2 Hours": "under_2_hours",
    "1 - 4 Weeks": "1_4_weeks",
    "1 - 3 Months": "1_3_months",
    "3 - 6 Months": "3_6_months",
}

_MISSING = {"", "nan", "null", "none", "n/a"}


class IngestionError(Exception):
    """Raised when the dataset does not look like the dataset we designed against."""


@dataclass(frozen=True)
class NormalizedCourse:
    course_id: str
    title: str
    organization: str
    url: str | None
    rating: float | None
    review_count: int | None
    review_count_raw: str
    review_count_is_approximate: bool
    students_enrolled: int | None
    difficulty: str | None
    course_type: str | None
    duration: str | None
    raw_skills: tuple[str, ...]
    skills: tuple[str, ...]
    technical_skills: tuple[str, ...]
    dropped_skills: tuple[str, ...]
    # Display only. Never read by recommendation logic -- the dataset audit found
    # rows carrying another course's description.
    description: str | None = None

    @property
    def has_technical_skills(self) -> bool:
        return bool(self.technical_skills)


@dataclass
class IngestionResult:
    courses: list[NormalizedCourse]
    unmapped_skill_counts: Counter = field(default_factory=Counter)
    skill_course_counts: Counter = field(default_factory=Counter)
    dataset_vocabulary: set[str] = field(default_factory=set)

    @property
    def courses_with_technical_skills(self) -> list[NormalizedCourse]:
        return [c for c in self.courses if c.has_technical_skills]

    @property
    def courses_without_technical_skills(self) -> list[NormalizedCourse]:
        return [c for c in self.courses if not c.has_technical_skills]


# --------------------------------------------------------------------------
# field parsers
# --------------------------------------------------------------------------

def _clean(value: str | None) -> str:
    return (value or "").strip()


def _optional(value: str | None) -> str | None:
    cleaned = _clean(value)
    return None if cleaned.lower() in _MISSING else cleaned


def split_skills(raw: str) -> list[str]:
    """Split the Skills field into stripped skill strings.

    Protects the one comma-containing skill name before splitting.
    """
    protected = raw.replace(_COMMA_SKILL, _COMMA_SKILL_PLACEHOLDER)
    parts = (part.strip().replace(_COMMA_SKILL_PLACEHOLDER, _COMMA_SKILL) for part in protected.split(","))
    return [part for part in parts if part]


def parse_rating(raw: str | None) -> float | None:
    cleaned = _optional(raw)
    if cleaned is None:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_review_count(raw: str | None) -> tuple[int | None, bool]:
    """Parse "411", "20K" or "8.1K".

    Returns (count, is_approximate). K-suffixed values lose precision in the
    source, so they are flagged rather than presented as exact.
    """
    cleaned = _optional(raw)
    if cleaned is None:
        return None, False
    compact = cleaned.replace(",", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KkMm]?)", compact)
    if not match:
        return None, False
    number, suffix = match.groups()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[suffix.lower()]
    return int(round(float(number) * multiplier)), suffix != ""


def parse_students_enrolled(raw: str | None) -> int | None:
    cleaned = _optional(raw)
    if cleaned is None:
        return None
    compact = cleaned.replace(",", "")
    return int(compact) if compact.isdigit() else None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "course"


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------

def read_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the raw CSV with a real CSV parser (descriptions contain newlines)."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_COLUMNS:
            raise IngestionError(
                "unexpected dataset columns.\n"
                f"  expected: {EXPECTED_COLUMNS}\n"
                f"  found:    {reader.fieldnames}"
            )
        return list(reader)


def dataset_vocabulary(rows: list[dict[str, str]]) -> set[str]:
    """Every distinct raw skill string present in the dataset."""
    vocabulary: set[str] = set()
    for row in rows:
        vocabulary.update(split_skills(row.get("Skills", "")))
    return vocabulary


def normalize_course(row: dict[str, str], kb: KnowledgeBase, course_id: str) -> NormalizedCourse:
    raw_skills = split_skills(row.get("Skills", ""))

    canonical: list[str] = []
    dropped: list[str] = []
    for raw_skill in raw_skills:
        skill_id = kb.canonical_skill_for(raw_skill)
        if skill_id is None:
            dropped.append(raw_skill)
        elif skill_id not in canonical:
            canonical.append(skill_id)

    technical = [s for s in canonical if not kb.skills[s].is_human_skill]
    review_count, approximate = parse_review_count(row.get("Review Count"))

    return NormalizedCourse(
        course_id=course_id,
        title=_clean(row.get("Title")),
        organization=_clean(row.get("Organization")),
        url=_optional(row.get("course_url")),
        rating=parse_rating(row.get("Ratings")),
        review_count=review_count,
        review_count_raw=_clean(row.get("Review Count")),
        review_count_is_approximate=approximate,
        students_enrolled=parse_students_enrolled(row.get("course_students_enrolled")),
        difficulty=DIFFICULTY_MAP.get(_clean(row.get("Difficulty"))),
        course_type=COURSE_TYPE_MAP.get(_clean(row.get("Type"))),
        duration=DURATION_MAP.get(_clean(row.get("Duration"))),
        raw_skills=tuple(raw_skills),
        skills=tuple(canonical),
        technical_skills=tuple(technical),
        dropped_skills=tuple(dropped),
        description=_optional(row.get("course_description")),
    )


def ingest(csv_path: Path, kb: KnowledgeBase) -> IngestionResult:
    """Read the dataset and normalize every row. Deterministic; no side effects."""
    rows = read_rows(csv_path)

    courses: list[NormalizedCourse] = []
    used_ids: dict[str, int] = {}
    unmapped: Counter = Counter()
    per_skill: Counter = Counter()

    for row in rows:
        # The leading index column is unstable, so ids come from the title, which
        # the audit confirmed is unique across all 623 rows. The suffix guard
        # keeps ids unique even if that ever stops being true.
        base_id = slugify(_clean(row.get("Title")))
        seen = used_ids.get(base_id, 0)
        used_ids[base_id] = seen + 1
        course_id = base_id if seen == 0 else f"{base_id}-{seen + 1}"

        course = normalize_course(row, kb, course_id)
        courses.append(course)
        unmapped.update(course.dropped_skills)
        per_skill.update(course.skills)

    return IngestionResult(
        courses=courses,
        unmapped_skill_counts=unmapped,
        skill_course_counts=per_skill,
        dataset_vocabulary=dataset_vocabulary(rows),
    )


def coverage_report(result: IngestionResult, kb: KnowledgeBase) -> dict[str, dict]:
    """How well the dataset covers each canonical skill.

    Computed from the data, never hand-written:
      good   >= 10 courses
      thin   1..9 courses
      proxy  no direct courses but `related` skills have some
      none   nothing available -- the application must say so
    """
    report: dict[str, dict] = {}
    for skill in kb.skills.values():
        count = result.skill_course_counts.get(skill.id, 0)
        if count >= 10:
            level = "good"
        elif count > 0:
            level = "thin"
        elif any(result.skill_course_counts.get(r, 0) > 0 for r in skill.related):
            level = "proxy"
        else:
            level = "none"
        report[skill.id] = {
            "name": skill.name,
            "kind": skill.kind,
            "course_count": count,
            "coverage": level,
            "proxy_skills": [r for r in skill.related if result.skill_course_counts.get(r, 0) > 0]
            if level == "proxy"
            else [],
        }
    return report
