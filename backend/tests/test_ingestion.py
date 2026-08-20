"""Tests for Coursera dataset parsing and normalization."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.ingestion.coursera import (
    IngestionError,
    coverage_report,
    ingest,
    normalize_course,
    parse_rating,
    parse_review_count,
    parse_students_enrolled,
    read_rows,
    slugify,
    split_skills,
)
from app.knowledge.loader import load_knowledge_base

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "coursera_course_dataset_v3.csv"
V2_DATASET = REPO_ROOT / "data" / "coursera_course_dataset_v2_no_null.csv"


class TestFieldParsers(unittest.TestCase):
    def test_split_skills_strips_whitespace(self):
        self.assertEqual(split_skills(" Python Programming, SQL "), ["Python Programming", "SQL"])

    def test_split_skills_keeps_the_comma_containing_skill_intact(self):
        parsed = split_skills(" SQL, Extract, Transform, Load, Big Data")
        self.assertEqual(parsed, ["SQL", "Extract, Transform, Load", "Big Data"])
        self.assertNotIn("Extract", parsed)
        self.assertNotIn("Transform", parsed)
        self.assertNotIn("Load", parsed)

    def test_split_skills_drops_empty_tokens(self):
        self.assertEqual(split_skills(" SQL, , Linux,"), ["SQL", "Linux"])

    def test_parse_rating(self):
        self.assertEqual(parse_rating("4.8"), 4.8)
        self.assertIsNone(parse_rating(""))
        self.assertIsNone(parse_rating("not a number"))

    def test_parse_review_count_exact(self):
        self.assertEqual(parse_review_count("411"), (411, False))
        self.assertEqual(parse_review_count("6"), (6, False))

    def test_parse_review_count_thousands(self):
        self.assertEqual(parse_review_count("20K"), (20_000, True))
        self.assertEqual(parse_review_count("8.1K"), (8_100, True))
        self.assertEqual(parse_review_count("137K"), (137_000, True))

    def test_parse_review_count_missing(self):
        self.assertEqual(parse_review_count(""), (None, False))
        self.assertEqual(parse_review_count(None), (None, False))

    def test_parse_students_enrolled(self):
        self.assertEqual(parse_students_enrolled("700,909"), 700_909)
        self.assertEqual(parse_students_enrolled("1567"), 1567)
        self.assertIsNone(parse_students_enrolled(""))

    def test_slugify(self):
        self.assertEqual(slugify("Google Project Management:"), "google-project-management")
        self.assertEqual(slugify("IBM & Darden Digital Strategy"), "ibm-darden-digital-strategy")


class TestNormalizeCourse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()

    def _row(self, **overrides):
        row = {
            "": "0",
            "Title": "Test Course",
            "Organization": "Test Org",
            "Skills": " Python Programming, Machine Learning, Finance, Leadership and Management",
            "Ratings": "4.7",
            "course_url": "https://www.coursera.org/learn/test",
            "course_students_enrolled": "12,345",
            "course_description": "Some text.",
            "Review Count": "8.1K",
            "Difficulty": "Intermediate",
            "Type": "Specialization",
            "Duration": "1 - 3 Months",
        }
        row.update(overrides)
        return row

    def test_maps_skills_to_canonical_ids(self):
        course = normalize_course(self._row(), self.kb, "test-course")
        self.assertIn("python", course.skills)
        self.assertIn("machine_learning", course.skills)

    def test_drops_out_of_scope_skills(self):
        course = normalize_course(self._row(), self.kb, "test-course")
        self.assertIn("Finance", course.dropped_skills)
        self.assertNotIn("Finance", course.skills)

    def test_human_skills_are_excluded_from_technical_skills(self):
        course = normalize_course(self._row(), self.kb, "test-course")
        self.assertIn("leadership", course.skills)
        self.assertNotIn("leadership", course.technical_skills)

    def test_normalizes_enums(self):
        course = normalize_course(self._row(), self.kb, "test-course")
        self.assertEqual(course.difficulty, "intermediate")
        self.assertEqual(course.course_type, "specialization")
        self.assertEqual(course.duration, "1_3_months")

    def test_missing_optional_fields_become_none(self):
        course = normalize_course(
            self._row(course_url="", course_students_enrolled="", course_description=""),
            self.kb,
            "test-course",
        )
        self.assertIsNone(course.url)
        self.assertIsNone(course.students_enrolled)
        self.assertIsNone(course.description)

    def test_missing_url_does_not_remove_skills(self):
        """Approved decision 13: a missing URL must not disqualify a course."""
        course = normalize_course(self._row(course_url=""), self.kb, "test-course")
        self.assertTrue(course.has_technical_skills)

    def test_review_count_approximation_is_flagged(self):
        approximate = normalize_course(self._row(), self.kb, "c")
        self.assertTrue(approximate.review_count_is_approximate)
        exact = normalize_course(self._row(**{"Review Count": "411"}), self.kb, "c")
        self.assertFalse(exact.review_count_is_approximate)

    def test_duplicate_canonical_skills_are_collapsed(self):
        row = self._row(Skills=" Machine Learning, Applied Machine Learning, Machine Learning Algorithms")
        course = normalize_course(row, self.kb, "c")
        self.assertEqual(course.skills.count("machine_learning"), 1)

    def test_raw_skills_are_preserved(self):
        course = normalize_course(self._row(), self.kb, "test-course")
        self.assertIn("Finance", course.raw_skills)
        self.assertIn("Python Programming", course.raw_skills)


@unittest.skipUnless(DATASET.exists(), "dataset not present")
class TestRealDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = load_knowledge_base()
        cls.result = ingest(DATASET, cls.kb)

    def test_reads_all_rows(self):
        self.assertEqual(len(self.result.courses), 623)

    def test_rejects_a_file_with_unexpected_columns(self):
        """v2 has different columns, so pointing the reader at it must fail loudly."""
        if not V2_DATASET.exists():
            self.skipTest("v2 not present")
        with self.assertRaises(IngestionError):
            read_rows(V2_DATASET)

    def test_course_ids_are_unique(self):
        ids = [c.course_id for c in self.result.courses]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_phantom_etl_skills_anywhere(self):
        for course in self.result.courses:
            for phantom in ("Extract", "Transform", "Load"):
                self.assertNotIn(phantom, course.raw_skills, course.title)

    def test_etl_skill_is_actually_found(self):
        with_etl = [c for c in self.result.courses if "Extract, Transform, Load" in c.raw_skills]
        self.assertEqual(len(with_etl), 35)
        for course in with_etl:
            self.assertIn("data_pipelines_etl", course.skills)

    def test_every_raw_skill_is_stripped(self):
        for course in self.result.courses:
            for raw_skill in course.raw_skills:
                self.assertEqual(raw_skill, raw_skill.strip())

    def test_every_mapped_skill_is_a_known_canonical_id(self):
        for course in self.result.courses:
            for skill_id in course.skills:
                self.assertIn(skill_id, self.kb.skills)

    def test_all_ratings_parse(self):
        self.assertTrue(all(c.rating is not None for c in self.result.courses))

    def test_all_review_counts_parse(self):
        self.assertTrue(all(c.review_count is not None for c in self.result.courses))

    def test_all_enums_normalize(self):
        for course in self.result.courses:
            self.assertIsNotNone(course.difficulty, course.title)
            self.assertIsNotNone(course.course_type, course.title)
            self.assertIsNotNone(course.duration, course.title)

    def test_unmapped_skills_are_all_declared_out_of_scope(self):
        for raw_skill in self.result.unmapped_skill_counts:
            self.assertIn(raw_skill, self.kb.out_of_scope_skills)

    def test_human_learning_is_dropped(self):
        self.assertIn("Human Learning", self.result.unmapped_skill_counts)
        for course in self.result.courses:
            self.assertNotIn("Human Learning", course.skills)

    def test_courses_without_a_url_are_still_ingested(self):
        without_url = [c for c in self.result.courses if c.url is None]
        self.assertEqual(len(without_url), 220)
        self.assertTrue(any(c.has_technical_skills for c in without_url))

    def test_zero_coverage_skills_report_none(self):
        coverage = coverage_report(self.result, self.kb)
        self.assertEqual(coverage["version_control"]["coverage"], "none")
        self.assertEqual(coverage["api_design"]["coverage"], "proxy")
        for skill_id in ("mlops", "model_deployment"):
            self.assertEqual(coverage[skill_id]["coverage"], "proxy")
            self.assertTrue(coverage[skill_id]["proxy_skills"])

    def test_ingestion_is_deterministic(self):
        again = ingest(DATASET, self.kb)
        self.assertEqual(
            [(c.course_id, c.skills) for c in self.result.courses],
            [(c.course_id, c.skills) for c in again.courses],
        )

    def test_raw_dataset_is_not_modified(self):
        """The ingestion must be read-only over the source file."""
        before = DATASET.read_bytes()
        ingest(DATASET, self.kb)
        self.assertEqual(DATASET.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
