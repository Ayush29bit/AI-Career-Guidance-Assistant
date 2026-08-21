"""initial schema

Creates the whole schema in one revision: the knowledge tables the seed fills
from skills.yaml, careers.yaml and the Coursera v3 dataset, and the application
tables that PostgreSQL itself owns (student profiles, conversations, messages).

Revision ID: 0001
Revises:
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Repeated from app.database.models so the migration never has to import
#: application code. app.database.seed asserts the two still agree.
_SKILL_KINDS = "'concept', 'language', 'tool', 'platform', 'human'"
_EXPERIENCE_LEVELS = "'beginner', 'intermediate', 'advanced'"
_COURSE_DIFFICULTIES = "'beginner', 'intermediate', 'advanced', 'mixed'"
_TAG_TYPES = "'interest', 'work_preference'"
_PROFILE_ENTRY_TYPES = "'strength', 'dislike', 'goal'"
_MESSAGE_ROLES = "'user', 'assistant', 'system'"

_NOW = sa.text("now()")


def upgrade() -> None:
    # ---------------------------------------------------------------- skills
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(f"kind IN ({_SKILL_KINDS})", name="ck_skills_kind"),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
    )
    op.create_index("ix_skills_kind", "skills", ["kind"])
    op.create_index("ix_skills_category", "skills", ["category"])

    op.create_table(
        "skill_prerequisites",
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("prerequisite_skill_id", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "skill_id <> prerequisite_skill_id", name="ck_skill_prerequisites_not_self"
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name="fk_skill_prerequisites_skill", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["prerequisite_skill_id"],
            ["skills.id"],
            name="fk_skill_prerequisites_prerequisite",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "skill_id", "prerequisite_skill_id", name="pk_skill_prerequisites"
        ),
    )

    op.create_table(
        "skill_relations",
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("related_skill_id", sa.String(length=64), nullable=False),
        sa.CheckConstraint("skill_id <> related_skill_id", name="ck_skill_relations_not_self"),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name="fk_skill_relations_skill", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_skill_id"],
            ["skills.id"],
            name="fk_skill_relations_related",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("skill_id", "related_skill_id", name="pk_skill_relations"),
    )

    # --------------------------------------------------------------- careers
    op.create_table(
        "careers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("short_description", sa.Text(), nullable=False),
        sa.Column("expected_experience", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            f"expected_experience IN ({_EXPERIENCE_LEVELS})",
            name="ck_careers_expected_experience",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_careers"),
    )

    op.create_table(
        "career_tags",
        sa.Column("career_id", sa.String(length=64), nullable=False),
        sa.Column("tag_type", sa.String(length=16), nullable=False),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.CheckConstraint(f"tag_type IN ({_TAG_TYPES})", name="ck_career_tags_type"),
        sa.ForeignKeyConstraint(
            ["career_id"], ["careers.id"], name="fk_career_tags_career", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("career_id", "tag_type", "tag", name="pk_career_tags"),
    )
    op.create_index("ix_career_tags_type_tag", "career_tags", ["tag_type", "tag"])

    op.create_table(
        "career_skills",
        sa.Column("career_id", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("required_level", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1", name="ck_career_skills_importance_range"
        ),
        sa.CheckConstraint(
            "required_level >= 0 AND required_level <= 1",
            name="ck_career_skills_required_level_range",
        ),
        sa.ForeignKeyConstraint(
            ["career_id"], ["careers.id"], name="fk_career_skills_career", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name="fk_career_skills_skill", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("career_id", "skill_id", name="pk_career_skills"),
    )
    op.create_index("ix_career_skills_skill_id", "career_skills", ["skill_id"])

    # --------------------------------------------------------------- courses
    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("review_count_raw", sa.String(length=32), nullable=True),
        sa.Column(
            "review_count_is_approximate",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("students_enrolled", sa.Integer(), nullable=True),
        sa.Column("difficulty", sa.String(length=16), nullable=True),
        sa.Column("course_type", sa.String(length=32), nullable=True),
        sa.Column("duration", sa.String(length=32), nullable=True),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment=(
                "Display only and untrusted: the dataset audit found rows carrying "
                "another course's text. Recommendation logic must not read this."
            ),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            f"difficulty IS NULL OR difficulty IN ({_COURSE_DIFFICULTIES})",
            name="ck_courses_difficulty",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 0 AND rating <= 5)", name="ck_courses_rating_range"
        ),
        sa.CheckConstraint(
            "review_count IS NULL OR review_count >= 0", name="ck_courses_review_count_range"
        ),
        sa.CheckConstraint(
            "students_enrolled IS NULL OR students_enrolled >= 0",
            name="ck_courses_students_enrolled_range",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
    )
    op.create_index("ix_courses_difficulty", "courses", ["difficulty"])
    op.create_index("ix_courses_organization", "courses", ["organization"])

    op.create_table(
        "course_skills",
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"], ["courses.id"], name="fk_course_skills_course", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name="fk_course_skills_skill", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("course_id", "skill_id", name="pk_course_skills"),
    )
    op.create_index("ix_course_skills_skill_id", "course_skills", ["skill_id"])

    # ------------------------------------------------------ student profiles
    op.create_table(
        "student_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("experience_level", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            f"experience_level IS NULL OR experience_level IN ({_EXPERIENCE_LEVELS})",
            name="ck_student_profiles_experience_level",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_student_profiles"),
    )

    op.create_table(
        "student_profile_skills",
        sa.Column("student_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("proficiency", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "proficiency >= 0 AND proficiency <= 1",
            name="ck_student_profile_skills_proficiency_range",
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_student_profile_skills_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name="fk_student_profile_skills_skill",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "student_profile_id", "skill_id", name="pk_student_profile_skills"
        ),
    )
    op.create_index("ix_student_profile_skills_skill_id", "student_profile_skills", ["skill_id"])

    op.create_table(
        "student_profile_tags",
        sa.Column("student_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_type", sa.String(length=16), nullable=False),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.CheckConstraint(f"tag_type IN ({_TAG_TYPES})", name="ck_student_profile_tags_type"),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_student_profile_tags_profile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "student_profile_id", "tag_type", "tag", name="pk_student_profile_tags"
        ),
    )

    op.create_table(
        "student_profile_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("student_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(
            f"entry_type IN ({_PROFILE_ENTRY_TYPES})", name="ck_student_profile_entries_type"
        ),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_student_profile_entries_profile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_student_profile_entries"),
    )
    op.create_index(
        "ix_student_profile_entries_profile_type",
        "student_profile_entries",
        ["student_profile_id", "entry_type"],
    )

    # --------------------------------------------------------- conversations
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["student_profile_id"],
            ["student_profiles.id"],
            name="fk_conversations_profile",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index(
        "ix_conversations_student_profile_id", "conversations", ["student_profile_id"]
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.CheckConstraint(f"role IN ({_MESSAGE_ROLES})", name="ck_messages_role"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    # Reverse creation order so no foreign key is ever left dangling.
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("student_profile_entries")
    op.drop_table("student_profile_tags")
    op.drop_table("student_profile_skills")
    op.drop_table("student_profiles")
    op.drop_table("course_skills")
    op.drop_table("courses")
    op.drop_table("career_skills")
    op.drop_table("career_tags")
    op.drop_table("careers")
    op.drop_table("skill_relations")
    op.drop_table("skill_prerequisites")
    op.drop_table("skills")
