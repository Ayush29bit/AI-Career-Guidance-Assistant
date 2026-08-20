"""Tests for the career knowledge base and its validation."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.ingestion.coursera import dataset_vocabulary, read_rows
from app.knowledge.loader import (
    KnowledgeBaseError,
    load_knowledge_base,
    validate_against_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "coursera_course_dataset_v3.csv"

APPROVED_CAREERS = {
    "ml_engineer",
    "data_scientist",
    "data_analyst",
    "data_engineer",
    "bi_analyst",
    "backend_engineer",
    "cloud_engineer",
    "devops_engineer",
    "cybersecurity_analyst",
    "technical_product_manager",
    "ux_designer",
}


class TestKnowledgeBaseLoads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_loads_without_problems(self):
        self.assertGreater(len(self.kb.skills), 0)
        self.assertGreater(len(self.kb.careers), 0)

    def test_exactly_the_eleven_approved_careers(self):
        self.assertEqual(set(self.kb.careers), APPROVED_CAREERS)

    def test_frontend_engineer_is_not_present(self):
        self.assertNotIn("frontend_engineer", self.kb.careers)

    def test_every_career_skill_reference_resolves(self):
        for career in self.kb.careers.values():
            for requirement in career.required_skills:
                self.assertIn(requirement.skill, self.kb.skills, career.id)

    def test_every_prerequisite_resolves(self):
        for skill in self.kb.skills.values():
            for prerequisite in skill.prerequisites:
                self.assertIn(prerequisite, self.kb.skills, skill.id)

    def test_enum_values_are_valid(self):
        for skill in self.kb.skills.values():
            self.assertIn(skill.category, self.kb.categories)
            self.assertIn(skill.kind, self.kb.kinds)
        for career in self.kb.careers.values():
            self.assertIn(career.expected_experience, self.kb.experience_levels)
            for tag in career.interest_tags:
                self.assertIn(tag, self.kb.interest_tags)
            for tag in career.work_tags:
                self.assertIn(tag, self.kb.work_tags)

    def test_weights_are_in_unit_interval(self):
        for career in self.kb.careers.values():
            for requirement in career.required_skills:
                self.assertGreaterEqual(requirement.importance, 0.0)
                self.assertLessEqual(requirement.importance, 1.0)
                self.assertGreaterEqual(requirement.required_level, 0.0)
                self.assertLessEqual(requirement.required_level, 1.0)

    def test_human_learning_is_not_an_alias(self):
        """Approved decision 3: only 48 of its 58 rows are actually ML courses."""
        self.assertIsNone(self.kb.canonical_skill_for("Human Learning"))
        self.assertIn("Human Learning", self.kb.out_of_scope_skills)

    def test_tensorflow_is_its_own_skill_not_an_alias_of_deep_learning(self):
        self.assertEqual(self.kb.canonical_skill_for("Tensorflow"), "tensorflow")
        self.assertIn("deep_learning", self.kb.skills["tensorflow"].prerequisites)

    def test_docker_and_kubernetes_are_separate_skills(self):
        self.assertEqual(self.kb.canonical_skill_for("Docker (Software)"), "docker")
        self.assertEqual(self.kb.canonical_skill_for("Kubernetes"), "kubernetes")
        self.assertIn("docker", self.kb.skills["kubernetes"].prerequisites)

    def test_zero_coverage_skills_are_retained(self):
        """Approved decision 6: keep them and report honestly."""
        for skill_id in ("mlops", "model_deployment", "version_control", "api_design"):
            self.assertIn(skill_id, self.kb.skills)
            self.assertFalse(self.kb.skills[skill_id].has_dataset_coverage)

    def test_zero_coverage_skills_are_actually_required_by_careers(self):
        required_anywhere = {
            requirement.skill
            for career in self.kb.careers.values()
            for requirement in career.required_skills
        }
        for skill_id in ("mlops", "model_deployment", "version_control", "api_design"):
            self.assertIn(skill_id, required_anywhere)

    def test_human_skills_are_flagged(self):
        for skill_id in ("communication", "leadership", "collaboration", "problem_solving"):
            self.assertTrue(self.kb.skills[skill_id].is_human_skill)
        self.assertNotIn("communication", self.kb.technical_skill_ids())

    def test_normalization_strips_whitespace(self):
        self.assertEqual(self.kb.canonical_skill_for("  Python Programming  "), "python")

    def test_normalization_is_deterministic_and_exact(self):
        """No fuzzy matching: a near-miss must not resolve."""
        self.assertEqual(self.kb.canonical_skill_for("Python Programming"), "python")
        self.assertIsNone(self.kb.canonical_skill_for("python programming"))
        self.assertIsNone(self.kb.canonical_skill_for("Python 3"))

    def test_ambiguous_tokens_are_not_mapped(self):
        """Tokens that mean two different things must not be forced into one skill.

        'Network Model' is Coursera's tag for neural network models as well as
        computer network models (8 of its 20 courses are ML courses), so mapping
        it to `networking` would put deep-learning courses in a security roadmap.
        'Apache' could be the web server, Spark or Kafka.
        """
        for token in ("Network Model", "Apache"):
            self.assertIsNone(self.kb.canonical_skill_for(token))
            self.assertIn(token, self.kb.out_of_scope_skills)

    def test_comma_containing_skill_is_mapped(self):
        self.assertEqual(
            self.kb.canonical_skill_for("Extract, Transform, Load"), "data_pipelines_etl"
        )


class TestPrerequisiteGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def test_prerequisites_form_a_dag(self):
        """Topological sort must consume every skill; leftovers mean a cycle."""
        remaining = {s.id: set(s.prerequisites) for s in self.kb.skills.values()}
        ordered: list[str] = []
        while remaining:
            ready = sorted(sid for sid, prereqs in remaining.items() if not prereqs)
            self.assertTrue(ready, f"prerequisite cycle among: {sorted(remaining)}")
            for sid in ready:
                del remaining[sid]
                ordered.append(sid)
            for prereqs in remaining.values():
                prereqs.difference_update(ready)
        self.assertEqual(len(ordered), len(self.kb.skills))

    def test_roadmap_order_is_sensible(self):
        """Genuine prerequisites must order correctly by depth.

        Note this checks only true prerequisite relations. Deep Learning and
        Model Deployment sit at the same depth on purpose: you do not need deep
        learning to deploy a model. The illustrative ML sequence in
        RECOMMENDATIONS.md blends prerequisites with gap priority, and the final
        roadmap order is the engine's job -- the DAG only encodes what must come
        first.
        """
        def depth(skill_id: str, seen: frozenset[str] = frozenset()) -> int:
            skill = self.kb.skills[skill_id]
            if not skill.prerequisites or skill_id in seen:
                return 0
            return 1 + max(depth(p, seen | {skill_id}) for p in skill.prerequisites)

        self.assertLess(depth("python"), depth("machine_learning"))
        self.assertLess(depth("machine_learning"), depth("deep_learning"))
        self.assertLess(depth("deep_learning"), depth("tensorflow"))
        self.assertLess(depth("machine_learning"), depth("model_deployment"))
        self.assertLess(depth("model_deployment"), depth("mlops"))
        self.assertLess(depth("docker"), depth("kubernetes"))
        self.assertLess(depth("data_visualization"), depth("tableau"))


class TestValidationCatchesBadEdits(unittest.TestCase):
    """The validator has to actually fail on the mistakes it claims to catch."""

    def _write(self, tmpdir: Path, skills: str, careers: str) -> tuple[Path, Path]:
        skills_file = tmpdir / "skills.yaml"
        careers_file = tmpdir / "careers.yaml"
        skills_file.write_text(skills, encoding="utf-8")
        careers_file.write_text(careers, encoding="utf-8")
        return skills_file, careers_file

    GOOD_SKILLS = """
version: 1
categories: [programming]
kinds: [concept, language, human]
skills:
  - id: python
    name: Python
    category: programming
    kind: language
    aliases: ["Python Programming"]
    prerequisites: []
out_of_scope_skills: []
"""

    GOOD_CAREERS = """
version: 1
interest_tags: [software]
work_tags: [building]
experience_levels: [beginner, intermediate, advanced]
careers:
  - id: dev
    name: Developer
    short_description: Writes software.
    expected_experience: beginner
    interest_tags: [software]
    work_tags: [building]
    required_skills:
      - {skill: python, importance: 0.9, required_level: 0.7}
"""

    def _load(self, skills: str, careers: str):
        import tempfile

        with tempfile.TemporaryDirectory() as raw_tmpdir:
            skills_file, careers_file = self._write(Path(raw_tmpdir), skills, careers)
            return load_knowledge_base(skills_file, careers_file)

    def test_baseline_fixture_is_valid(self):
        kb = self._load(self.GOOD_SKILLS, self.GOOD_CAREERS)
        self.assertEqual(set(kb.skills), {"python"})

    def test_unknown_skill_id_in_career_is_rejected(self):
        bad = self.GOOD_CAREERS.replace("skill: python", "skill: rust")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(self.GOOD_SKILLS, bad)
        self.assertIn("unknown skill id 'rust'", str(ctx.exception))

    def test_unknown_prerequisite_is_rejected(self):
        bad = self.GOOD_SKILLS.replace("prerequisites: []", "prerequisites: [ghost]")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("unknown prerequisite skill id 'ghost'", str(ctx.exception))

    def test_invalid_enum_value_is_rejected(self):
        bad = self.GOOD_CAREERS.replace("expected_experience: beginner", "expected_experience: wizard")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(self.GOOD_SKILLS, bad)
        self.assertIn("invalid expected_experience", str(ctx.exception))

    def test_invalid_category_is_rejected(self):
        bad = self.GOOD_SKILLS.replace("category: programming", "category: sorcery")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("invalid category", str(ctx.exception))

    def test_invalid_interest_tag_is_rejected(self):
        bad = self.GOOD_CAREERS.replace("interest_tags: [software]\n    work_tags", "interest_tags: [vibes]\n    work_tags")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(self.GOOD_SKILLS, bad)
        self.assertIn("invalid interest tag", str(ctx.exception))

    def test_out_of_range_importance_is_rejected(self):
        bad = self.GOOD_CAREERS.replace("importance: 0.9", "importance: 1.7")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(self.GOOD_SKILLS, bad)
        self.assertIn("not in 0..1", str(ctx.exception))

    def test_duplicate_skill_id_is_rejected(self):
        bad = self.GOOD_SKILLS + """
  - id: python
    name: Python again
    category: programming
    kind: language
    aliases: []
    prerequisites: []
"""
        # the duplicate has to sit inside the skills list, so rebuild it
        bad = self.GOOD_SKILLS.replace(
            "out_of_scope_skills: []",
            """  - id: python
    name: Python again
    category: programming
    kind: language
    aliases: []
    prerequisites: []
out_of_scope_skills: []""",
        )
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("duplicate skill id 'python'", str(ctx.exception))

    def test_alias_claimed_by_two_skills_is_rejected(self):
        bad = self.GOOD_SKILLS.replace(
            "out_of_scope_skills: []",
            """  - id: python2
    name: Python Two
    category: programming
    kind: language
    aliases: ["Python Programming"]
    prerequisites: []
out_of_scope_skills: []""",
        )
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("is claimed by both", str(ctx.exception))

    def test_alias_also_listed_out_of_scope_is_rejected(self):
        bad = self.GOOD_SKILLS.replace(
            "out_of_scope_skills: []", 'out_of_scope_skills: ["Python Programming"]'
        )
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("out of scope", str(ctx.exception))

    def test_prerequisite_cycle_is_rejected(self):
        cyclic = """
version: 1
categories: [programming]
kinds: [concept]
skills:
  - id: a
    name: A
    category: programming
    kind: concept
    aliases: []
    prerequisites: [b]
  - id: b
    name: B
    category: programming
    kind: concept
    aliases: []
    prerequisites: [c]
  - id: c
    name: C
    category: programming
    kind: concept
    aliases: []
    prerequisites: [a]
out_of_scope_skills: []
"""
        careers = self.GOOD_CAREERS.replace("skill: python", "skill: a")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(cyclic, careers)
        self.assertIn("prerequisite cycle", str(ctx.exception))

    def test_self_prerequisite_is_rejected(self):
        bad = self.GOOD_SKILLS.replace("prerequisites: []", "prerequisites: [python]")
        with self.assertRaises(KnowledgeBaseError) as ctx:
            self._load(bad, self.GOOD_CAREERS)
        self.assertIn("lists itself as a prerequisite", str(ctx.exception))


@unittest.skipUnless(DATASET.exists(), "dataset not present")
class TestAliasesAgainstRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()
        cls.vocabulary = dataset_vocabulary(read_rows(DATASET))

    def test_every_alias_exists_in_the_dataset(self):
        missing = [a for a in self.kb.alias_map if a not in self.vocabulary]
        self.assertEqual(missing, [], f"aliases not present in the dataset: {missing}")

    def test_every_dataset_skill_is_accounted_for(self):
        """Exact partition: mapped, or explicitly out of scope. Nothing silent."""
        unaccounted = self.vocabulary - set(self.kb.alias_map) - self.kb.out_of_scope_skills
        self.assertEqual(unaccounted, set(), f"unaccounted dataset skills: {sorted(unaccounted)}")

    def test_out_of_scope_entries_are_real_dataset_tokens(self):
        phantom = [t for t in self.kb.out_of_scope_skills if t not in self.vocabulary]
        self.assertEqual(phantom, [], f"out-of-scope entries not in the dataset: {phantom}")

    def test_validate_against_dataset_reports_nothing(self):
        self.assertEqual(validate_against_dataset(self.kb, self.vocabulary), [])

    def test_a_typo_alias_would_be_caught(self):
        problems = validate_against_dataset(self.kb, self.vocabulary - {"Python Programming"})
        self.assertTrue(any("Python Programming" in p for p in problems))

    def test_a_new_dataset_token_would_be_caught(self):
        problems = validate_against_dataset(self.kb, self.vocabulary | {"Quantum Computing"})
        self.assertTrue(any("Quantum Computing" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
