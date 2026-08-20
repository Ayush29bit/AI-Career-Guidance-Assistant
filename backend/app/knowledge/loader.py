"""Load and validate the career knowledge base.

The knowledge base is two hand-edited YAML files. Everything downstream (the
recommendation engine, the ingestion script, the future database seed) reads it
through here, so a malformed edit fails loudly at load time instead of producing
quietly wrong recommendations.

Validation collects *all* problems and raises once, so a reviewer fixing a bad
edit sees the whole list rather than one error per run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).resolve().parent
SKILLS_FILE = KNOWLEDGE_DIR / "skills.yaml"
CAREERS_FILE = KNOWLEDGE_DIR / "careers.yaml"


class KnowledgeBaseError(Exception):
    """Raised when the knowledge base files are invalid."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        joined = "\n  - ".join(problems)
        super().__init__(f"{len(problems)} knowledge base problem(s):\n  - {joined}")


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    kind: str
    aliases: tuple[str, ...]
    prerequisites: tuple[str, ...]
    related: tuple[str, ...] = ()

    @property
    def is_human_skill(self) -> bool:
        """Human skills count towards career fit but never drive course recs."""
        return self.kind == "human"

    @property
    def has_dataset_coverage(self) -> bool:
        return bool(self.aliases)


@dataclass(frozen=True)
class RequiredSkill:
    skill: str
    importance: float
    required_level: float


@dataclass(frozen=True)
class Career:
    id: str
    name: str
    short_description: str
    expected_experience: str
    interest_tags: tuple[str, ...]
    work_tags: tuple[str, ...]
    required_skills: tuple[RequiredSkill, ...]


@dataclass
class KnowledgeBase:
    skills: dict[str, Skill]
    careers: dict[str, Career]
    out_of_scope_skills: frozenset[str]
    categories: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    interest_tags: tuple[str, ...] = ()
    work_tags: tuple[str, ...] = ()
    experience_levels: tuple[str, ...] = ()
    alias_map: dict[str, str] = field(default_factory=dict)

    def canonical_skill_for(self, raw_skill: str) -> str | None:
        """Resolve one raw Coursera skill string to a canonical skill id.

        Deterministic dictionary lookup on the exact (whitespace-stripped)
        string. Returns None for tokens we intentionally do not map.
        """
        return self.alias_map.get(raw_skill.strip())

    def technical_skill_ids(self) -> set[str]:
        return {s.id for s in self.skills.values() if not s.is_human_skill}

    def skills_without_coverage(self) -> list[Skill]:
        return [s for s in self.skills.values() if not s.has_dataset_coverage]


# --------------------------------------------------------------------------
# validation helpers
# --------------------------------------------------------------------------

def _is_unit_interval(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def _find_prerequisite_cycles(prereqs: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """Return every prerequisite cycle found, as lists of skill ids.

    Plain iterative DFS with a colour marking; the graph is ~60 nodes, so there
    is no reason for anything cleverer.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in prereqs}
    cycles: list[list[str]] = []

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour[node] != WHITE:
                    continue
                colour[node] = GREY
                path.append(node)
            neighbours = prereqs.get(node, ())
            if index < len(neighbours):
                stack.append((node, index + 1))
                nxt = neighbours[index]
                if nxt not in colour:
                    continue  # unknown id; reported separately
                if colour[nxt] == GREY:
                    cycle = path[path.index(nxt):] + [nxt]
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif colour[nxt] == WHITE:
                    stack.append((nxt, 0))
            else:
                colour[node] = BLACK
                path.pop()

    for node in prereqs:
        if colour[node] == WHITE:
            visit(node)
    return cycles


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise KnowledgeBaseError([f"{path.name}: expected a mapping at the top level"])
    return data


def load_knowledge_base(
    skills_file: Path = SKILLS_FILE,
    careers_file: Path = CAREERS_FILE,
) -> KnowledgeBase:
    """Load both files and validate them. Raises KnowledgeBaseError on any problem."""
    skills_doc = _read_yaml(skills_file)
    careers_doc = _read_yaml(careers_file)

    problems: list[str] = []

    categories = tuple(skills_doc.get("categories") or ())
    kinds = tuple(skills_doc.get("kinds") or ())
    interest_tags = tuple(careers_doc.get("interest_tags") or ())
    work_tags = tuple(careers_doc.get("work_tags") or ())
    experience_levels = tuple(careers_doc.get("experience_levels") or ())

    for name, values in (
        ("categories", categories),
        ("kinds", kinds),
        ("interest_tags", interest_tags),
        ("work_tags", work_tags),
        ("experience_levels", experience_levels),
    ):
        if not values:
            problems.append(f"enum '{name}' is missing or empty")

    # ---- skills -----------------------------------------------------------
    skills: dict[str, Skill] = {}
    alias_map: dict[str, str] = {}

    for entry in skills_doc.get("skills") or []:
        skill_id = entry.get("id")
        if not skill_id:
            problems.append(f"skill entry without an id: {entry!r}")
            continue
        if skill_id in skills:
            problems.append(f"duplicate skill id '{skill_id}'")
            continue
        if not entry.get("name"):
            problems.append(f"skill '{skill_id}': missing name")
        if categories and entry.get("category") not in categories:
            problems.append(
                f"skill '{skill_id}': invalid category {entry.get('category')!r} "
                f"(allowed: {', '.join(categories)})"
            )
        if kinds and entry.get("kind") not in kinds:
            problems.append(
                f"skill '{skill_id}': invalid kind {entry.get('kind')!r} "
                f"(allowed: {', '.join(kinds)})"
            )

        aliases = tuple(entry.get("aliases") or ())
        for alias in aliases:
            if not isinstance(alias, str) or alias != alias.strip():
                problems.append(f"skill '{skill_id}': alias {alias!r} must be a stripped string")
                continue
            if alias in alias_map:
                problems.append(
                    f"alias {alias!r} is claimed by both '{alias_map[alias]}' and '{skill_id}'"
                )
                continue
            alias_map[alias] = skill_id

        skills[skill_id] = Skill(
            id=skill_id,
            name=entry.get("name", skill_id),
            category=entry.get("category", ""),
            kind=entry.get("kind", ""),
            aliases=aliases,
            prerequisites=tuple(entry.get("prerequisites") or ()),
            related=tuple(entry.get("related") or ()),
        )

    if not skills:
        problems.append("no skills defined")

    # unknown skill ids inside the taxonomy itself
    for skill in skills.values():
        for prerequisite in skill.prerequisites:
            if prerequisite not in skills:
                problems.append(f"skill '{skill.id}': unknown prerequisite skill id '{prerequisite}'")
            elif prerequisite == skill.id:
                problems.append(f"skill '{skill.id}': lists itself as a prerequisite")
        for relative in skill.related:
            if relative not in skills:
                problems.append(f"skill '{skill.id}': unknown related skill id '{relative}'")

    for cycle in _find_prerequisite_cycles({s.id: s.prerequisites for s in skills.values()}):
        problems.append("prerequisite cycle: " + " -> ".join(cycle))

    # ---- out-of-scope list ------------------------------------------------
    out_of_scope = frozenset(skills_doc.get("out_of_scope_skills") or ())
    overlap = out_of_scope & set(alias_map)
    for token in sorted(overlap):
        problems.append(
            f"raw skill {token!r} is both an alias of '{alias_map[token]}' and listed as out of scope"
        )

    # ---- careers ----------------------------------------------------------
    careers: dict[str, Career] = {}

    for entry in careers_doc.get("careers") or []:
        career_id = entry.get("id")
        if not career_id:
            problems.append(f"career entry without an id: {entry!r}")
            continue
        if career_id in careers:
            problems.append(f"duplicate career id '{career_id}'")
            continue
        if not entry.get("name"):
            problems.append(f"career '{career_id}': missing name")
        if not entry.get("short_description"):
            problems.append(f"career '{career_id}': missing short_description")

        experience = entry.get("expected_experience")
        if experience_levels and experience not in experience_levels:
            problems.append(
                f"career '{career_id}': invalid expected_experience {experience!r} "
                f"(allowed: {', '.join(experience_levels)})"
            )

        career_interest_tags = tuple(entry.get("interest_tags") or ())
        career_work_tags = tuple(entry.get("work_tags") or ())
        if not career_interest_tags:
            problems.append(f"career '{career_id}': no interest_tags")
        if not career_work_tags:
            problems.append(f"career '{career_id}': no work_tags")
        for tag in career_interest_tags:
            if interest_tags and tag not in interest_tags:
                problems.append(f"career '{career_id}': invalid interest tag {tag!r}")
        for tag in career_work_tags:
            if work_tags and tag not in work_tags:
                problems.append(f"career '{career_id}': invalid work tag {tag!r}")

        required: list[RequiredSkill] = []
        seen_skills: set[str] = set()
        for requirement in entry.get("required_skills") or []:
            skill_id = requirement.get("skill")
            if skill_id not in skills:
                problems.append(f"career '{career_id}': unknown skill id '{skill_id}'")
                continue
            if skill_id in seen_skills:
                problems.append(f"career '{career_id}': skill '{skill_id}' listed twice")
                continue
            seen_skills.add(skill_id)

            importance = requirement.get("importance")
            level = requirement.get("required_level")
            if not _is_unit_interval(importance):
                problems.append(
                    f"career '{career_id}' skill '{skill_id}': importance {importance!r} not in 0..1"
                )
                continue
            if not _is_unit_interval(level):
                problems.append(
                    f"career '{career_id}' skill '{skill_id}': required_level {level!r} not in 0..1"
                )
                continue
            required.append(
                RequiredSkill(skill=skill_id, importance=float(importance), required_level=float(level))
            )

        if not required:
            problems.append(f"career '{career_id}': no valid required_skills")

        careers[career_id] = Career(
            id=career_id,
            name=entry.get("name", career_id),
            short_description=(entry.get("short_description") or "").strip(),
            expected_experience=experience or "",
            interest_tags=career_interest_tags,
            work_tags=career_work_tags,
            required_skills=tuple(required),
        )

    if not careers:
        problems.append("no careers defined")

    if problems:
        raise KnowledgeBaseError(problems)

    return KnowledgeBase(
        skills=skills,
        careers=careers,
        out_of_scope_skills=out_of_scope,
        categories=categories,
        kinds=kinds,
        interest_tags=interest_tags,
        work_tags=work_tags,
        experience_levels=experience_levels,
        alias_map=alias_map,
    )


def validate_against_dataset(kb: KnowledgeBase, dataset_vocabulary: set[str]) -> list[str]:
    """Check the alias map against the raw skill strings actually in the dataset.

    Asserts an exact partition: every raw token is either an alias or explicitly
    listed as out of scope, and every alias really exists in the dataset. This is
    what stops a typo from silently dropping courses, and stops a newly appearing
    token from vanishing unnoticed.
    """
    problems: list[str] = []

    for alias, skill_id in sorted(kb.alias_map.items()):
        if alias not in dataset_vocabulary:
            problems.append(f"alias {alias!r} (skill '{skill_id}') does not exist in the dataset")

    for token in sorted(kb.out_of_scope_skills):
        if token not in dataset_vocabulary:
            problems.append(f"out-of-scope entry {token!r} does not exist in the dataset")

    unaccounted = dataset_vocabulary - set(kb.alias_map) - kb.out_of_scope_skills
    for token in sorted(unaccounted):
        problems.append(
            f"dataset skill {token!r} is neither mapped nor listed as out of scope"
        )

    return problems
