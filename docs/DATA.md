# AI Career Counsellor — Data & Knowledge

## 1. Purpose

The application uses two main knowledge sources:

1. **Career → Skills** — what skills different careers require.
2. **Course → Skills** — what skills are taught by courses in the provided Coursera dataset.

Together they enable:

```text id="v9j3o1"
Student Profile
      ↓
Career Recommendation
      ↓
Required Skills
      ↓
Skill Gap
      ↓
Relevant Courses
      ↓
Learning Roadmap
```

---

# 2. Career Knowledge Base

The application needs a curated set of career paths and their associated skills.

Example:

```text id="5z9s3f"
Machine Learning Engineer
├── Python
├── Machine Learning
├── Deep Learning
├── Model Deployment
├── MLOps
└── Cloud
```

Each career should contain:

- Career name
- Short description
- Required skills
- Skill importance/weight
- Optional expected proficiency level

For the prototype, prefer **a focused set of well-defined careers** over a huge career database.

Quality is more important than quantity.

---

# 3. Skill Taxonomy

Skills should use consistent names.

For example, avoid treating:

```text
Python
python programming
Python Programming
Python 3
```

as completely unrelated skills.

Normalize skills into canonical names where appropriate.

Example:

```text id="1r8r4v"
"python programming" → "Python"
"machine learning" → "Machine Learning"
"deep-learning"     → "Deep Learning"
```

The taxonomy should remain simple.

Do not build an unnecessarily complex ontology.

---

# 4. Coursera Dataset

The provided **Coursera Courses & Skills dataset 2024** is the primary learning-resource dataset.

It provides course information and associated skills.

The dataset should be cleaned and normalized before being used by the application.

Relevant information may include:

- Course title
- Organization
- Skills
- Rating
- Review count
- Difficulty
- Duration
- Other useful course metadata available in the dataset

The exact available fields should be verified from the actual dataset before implementation.

---

# 5. Coursera Data Relationship

The important relationship is:

```text id="9c6f8h"
Course
  │
  ├── Skill A
  ├── Skill B
  └── Skill C
```

This allows the system to answer:

> "Which courses can help this student close their skill gap?"

Example:

```text id="8x1lko"
Skill Gap:
Deep Learning
MLOps
Cloud

        ↓

Find courses covering these skills

        ↓

Rank suitable courses
```

---

# 6. Student Profile Data

The student's profile is created primarily from conversation.

Potential fields:

```text id="xk5m8p"
Skills
Skill proficiency/confidence
Interests
Preferences
Strengths
Dislikes
Experience
Goals
```

The user should not have to manually populate all of these.

The LLM extracts meaningful information from the conversation.

---

# 7. Confidence

Information can have different confidence levels.

Example:

```text id="6t4v8e"
Python
→ Explicitly stated
→ High confidence

Machine Learning
→ Mentioned through project experience
→ High/medium confidence

Interest in Research
→ Inferred from conversation
→ Lower confidence
```

Do not treat weak inference as an established fact.

### How this is stored

Each profile fact carries a `source` of `explicit` or `inferred`:

```text id="p9c2wq"
student_profile_skills.source
student_profile_tags.source
student_profile_entries.source
student_profiles.experience_level_source
```

`explicit` means the student stated it; `inferred` means the conversation layer
concluded it. The merge rule is that an inference never overwrites a statement,
while like replaces like -- the newer of two statements wins, as does the newer
of two inferences.

The recommendation engine does not read these columns. Scoring treats every
stored fact the same; the distinction exists to stop a weak inference silently
replacing something the student said.

Deletion only happens when the student withdraws something ("actually, I don't
enjoy statistics"). A fact is never dropped merely because a later turn did not
mention it.

---

# 8. Data Flow

### Career recommendation

```text id="3s6b2f"
Student Profile
      ↓
Career + Required Skills
      ↓
Career Matching
      ↓
Ranked Careers
```

### Skill gap

```text id="3i8c9n"
Selected Career
      ↓
Required Skills
      ↓
Student Skills
      ↓
Skill Differences
      ↓
Priority Gaps
```

### Course recommendation

```text id="1g4j2z"
Priority Skill Gap
      ↓
Courses covering the skill
      ↓
Filter / Rank
      ↓
Recommended Courses
```

---

# 9. Data Ownership

Use clear boundaries.

### Career Knowledge Base

Source of truth for:

> **What skills are relevant to a career?**

### Coursera Dataset

Source of truth for:

> **What courses/resources cover which skills?**

### Student Profile

Source of truth for:

> **What the application currently knows about the student.**

### LLM

Not a source of truth for structured career/course data.

The LLM may explain data, but should not fabricate it.

---

# 10. Data Preparation

Before using the Coursera dataset:

1. Inspect the actual schema.
2. Remove unusable records.
3. Normalize skill names.
4. Handle missing values.
5. Remove obvious duplicates.
6. Convert data into a format convenient for PostgreSQL.
7. Seed the cleaned data into the database.

Keep the preprocessing pipeline simple and reproducible.

Do not manually modify hundreds of records in application code.

---

# 11. Data Quality

Recommendations are only as good as the underlying data.

Therefore:

- Avoid duplicate skills.
- Avoid inconsistent career names.
- Validate career-skill mappings.
- Validate course-skill mappings.
- Handle missing course metadata.
- Do not recommend courses that have no meaningful relationship to the identified skill gap.

If the dataset does not contain enough relevant courses for a particular skill, the system should say so rather than inventing a course.

---

# 12. Prototype Scope

Do not attempt to create an enormous career ontology.

A focused prototype with a manageable number of careers and skills is preferred.

The goal is:

> **High-quality recommendations for the careers we support.**

Not:

> **A database containing every occupation on Earth.**

---

# 13. Important Constraint

The Coursera dataset **does not by itself solve career recommendation**.

It primarily provides:

```text id="2j4h5w"
Course → Skills
```

Our application adds:

```text id="4v8k2c"
Career → Skills
```

The combination creates:

```text id="0l3m9a"
Student
  ↓
Career
  ↓
Skills
  ↓
Courses
```

This distinction must remain clear throughout implementation.