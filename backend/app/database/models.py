"""SQLAlchemy models for the AI Career Counsellor.

Two groups of tables live here.

**Knowledge tables** (skills, careers, courses and their join tables) are a
projection of data that is owned elsewhere: skills.yaml and careers.yaml own the
taxonomy, and the Coursera v3 ingestion owns the catalogue. Nothing writes to
them except `app.database.seed`, and re-running the seed rebuilds them exactly.
They exist so the API, conversation layer and frontend can query career and
course data without every request re-parsing YAML and a 623-row CSV.

**Application tables** (student profiles, conversations, messages) are the
opposite: PostgreSQL owns them, and they are the reason the database exists.

The recommendation engine reads neither group. It keeps consuming the in-memory
KnowledgeBase and CourseCatalogue, so the deterministic scoring path is unchanged
and stays testable without a database.

Two conventions worth knowing before reading further:

* Skills are stored by canonical id (`python`, `deep_learning`). Raw Coursera
  skill strings are never stored -- normalization happens once, deterministically,
  during ingestion.
* Several small tables use a `*_type` discriminator column (`career_tags`,
  `student_profile_tags`, `student_profile_entries`) instead of a separate table
  per variant. The variants are structurally identical, and one table each keeps
  the schema small without losing any constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# --------------------------------------------------------------------------
# closed vocabularies
#
# These mirror enums that skills.yaml, careers.yaml and the ingestion module
# already define. They are repeated here because a CHECK constraint needs
# literal values at migration time, and a migration must never depend on parsing
# a YAML file. This is a copy of a *vocabulary*, not of data -- no skill, career
# or course is defined in Python. `app.database.seed` asserts these tuples still
# agree with the YAML on every run, so drift is caught immediately.
# --------------------------------------------------------------------------

SKILL_KINDS = ("concept", "language", "tool", "platform", "human")
EXPERIENCE_LEVELS = ("beginner", "intermediate", "advanced")
COURSE_DIFFICULTIES = ("beginner", "intermediate", "advanced", "mixed")

#: Applies to career_tags and student_profile_tags alike.
TAG_TYPES = ("interest", "work_preference")

#: Free-text profile facts the conversation layer will collect. These have no
#: controlled vocabulary, which is exactly why they are not tags.
PROFILE_ENTRY_TYPES = ("strength", "dislike", "goal")

#: How a profile fact reached us. 'explicit' means the student stated it;
#: 'inferred' means the conversation layer concluded it from what they said.
#: The recommendation engine scores both identically -- it never sees this
#: column. It exists so a weak inference cannot silently overwrite something
#: the student said outright (app.conversation.merge).
PROFILE_SOURCES = ("explicit", "inferred")

MESSAGE_ROLES = ("user", "assistant", "system")


def _one_of(column: str, allowed: tuple[str, ...]) -> str:
    """SQL fragment restricting a column to a closed vocabulary."""
    values = ", ".join(f"'{value}'" for value in allowed)
    return f"{column} IN ({values})"


class Base(DeclarativeBase):
    """Declarative base. Alembic autogenerate reads Base.metadata."""


class TimestampMixin:
    """created_at / updated_at, both database-generated and timezone-aware.

    Defaults are server-side so rows written by the seed's bulk statements --
    which bypass the ORM -- are stamped just like ORM-created rows.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# --------------------------------------------------------------------------
# knowledge: skills
# --------------------------------------------------------------------------

class Skill(Base, TimestampMixin):
    """One canonical skill. `id` is the snake_case id from skills.yaml."""

    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(_one_of("kind", SKILL_KINDS), name="ck_skills_kind"),
        Index("ix_skills_kind", "kind"),
        Index("ix_skills_category", "category"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    #: kind == 'human' marks a skill that counts towards career fit but must
    #: never drive course recommendations. Query it as `kind <> 'human'` rather
    #: than storing a second derived column.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    prerequisites: Mapped[list[SkillPrerequisite]] = relationship(
        back_populates="skill",
        foreign_keys="SkillPrerequisite.skill_id",
        cascade="all, delete-orphan",
    )
    related: Mapped[list[SkillRelation]] = relationship(
        back_populates="skill",
        foreign_keys="SkillRelation.skill_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Skill {self.id}>"


class SkillPrerequisite(Base):
    """`skill_id` should be learned after `prerequisite_skill_id`.

    The edges form the DAG that roadmap sequencing uses. Cycles are rejected by
    the knowledge base validator before anything reaches the database.
    """

    __tablename__ = "skill_prerequisites"
    __table_args__ = (
        CheckConstraint(
            "skill_id <> prerequisite_skill_id", name="ck_skill_prerequisites_not_self"
        ),
    )

    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    prerequisite_skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )

    skill: Mapped[Skill] = relationship(
        back_populates="prerequisites", foreign_keys=[skill_id]
    )
    prerequisite: Mapped[Skill] = relationship(foreign_keys=[prerequisite_skill_id])


class SkillRelation(Base):
    """An adjacent skill, used only to offer proxy courses.

    A related skill is never treated as equivalent to the skill itself: it is
    what lets the application say "there is no course for MLOps, but here is one
    on an adjacent topic" instead of silently substituting.
    """

    __tablename__ = "skill_relations"
    __table_args__ = (
        CheckConstraint("skill_id <> related_skill_id", name="ck_skill_relations_not_self"),
    )

    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    related_skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )

    skill: Mapped[Skill] = relationship(back_populates="related", foreign_keys=[skill_id])
    related_skill: Mapped[Skill] = relationship(foreign_keys=[related_skill_id])


# --------------------------------------------------------------------------
# knowledge: careers
# --------------------------------------------------------------------------

class Career(Base, TimestampMixin):
    __tablename__ = "careers"
    __table_args__ = (
        CheckConstraint(
            _one_of("expected_experience", EXPERIENCE_LEVELS),
            name="ck_careers_expected_experience",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_experience: Mapped[str] = mapped_column(String(16), nullable=False)

    tags: Mapped[list[CareerTag]] = relationship(
        back_populates="career", cascade="all, delete-orphan"
    )
    required_skills: Mapped[list[CareerSkill]] = relationship(
        back_populates="career", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Career {self.id}>"


class CareerTag(Base):
    """An interest or work-style tag on a career, from the careers.yaml enums."""

    __tablename__ = "career_tags"
    __table_args__ = (
        CheckConstraint(_one_of("tag_type", TAG_TYPES), name="ck_career_tags_type"),
        Index("ix_career_tags_type_tag", "tag_type", "tag"),
    )

    career_id: Mapped[str] = mapped_column(
        ForeignKey("careers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), primary_key=True)

    career: Mapped[Career] = relationship(back_populates="tags")


class CareerSkill(Base):
    """What a career requires, and how much.

    `importance` and `required_level` stay separate on purpose: a skill can
    matter a great deal to a role while only being needed at a shallow level.
    Scoring uses importance; gap size uses required_level.
    """

    __tablename__ = "career_skills"
    __table_args__ = (
        CheckConstraint(
            "importance >= 0 AND importance <= 1", name="ck_career_skills_importance_range"
        ),
        CheckConstraint(
            "required_level >= 0 AND required_level <= 1",
            name="ck_career_skills_required_level_range",
        ),
        Index("ix_career_skills_skill_id", "skill_id"),
    )

    career_id: Mapped[str] = mapped_column(
        ForeignKey("careers.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    required_level: Mapped[float] = mapped_column(Float, nullable=False)

    career: Mapped[Career] = relationship(back_populates="required_skills")
    skill: Mapped[Skill] = relationship()


# --------------------------------------------------------------------------
# knowledge: courses
# --------------------------------------------------------------------------

class Course(Base, TimestampMixin):
    """One normalized Coursera course.

    `id` is the title slug produced by ingestion. The dataset's own leading index
    column is unstable between versions and is deliberately not used.
    """

    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            f"difficulty IS NULL OR {_one_of('difficulty', COURSE_DIFFICULTIES)}",
            name="ck_courses_difficulty",
        ),
        CheckConstraint(
            "rating IS NULL OR (rating >= 0 AND rating <= 5)", name="ck_courses_rating_range"
        ),
        CheckConstraint(
            "review_count IS NULL OR review_count >= 0", name="ck_courses_review_count_range"
        ),
        CheckConstraint(
            "students_enrolled IS NULL OR students_enrolled >= 0",
            name="ck_courses_students_enrolled_range",
        ),
        Index("ix_courses_difficulty", "difficulty"),
        Index("ix_courses_organization", "organization"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Missing on roughly a third of the catalogue. The UI must handle a course
    #: with no link rather than inventing one.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The source string ("411", "20K"), kept so the exact value is never lost.
    review_count_raw: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: True when the source stored an approximation such as "20K", so the UI can
    #: present it as approximate instead of implying precision it does not have.
    review_count_is_approximate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    students_enrolled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    course_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Display only and untrusted: the dataset audit found rows carrying "
            "another course's text. Recommendation logic must not read this."
        ),
    )

    skills: Mapped[list[CourseSkill]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Course {self.id}>"


class CourseSkill(Base):
    """A canonical skill taught by a course.

    Every raw Coursera skill string was resolved to a canonical id at ingestion,
    or dropped as out of scope. Nothing raw is stored. Human skills are included
    here for completeness; filter them out with `skills.kind <> 'human'` when
    driving course recommendations.
    """

    __tablename__ = "course_skills"
    __table_args__ = (Index("ix_course_skills_skill_id", "skill_id"),)

    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )

    course: Mapped[Course] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


# --------------------------------------------------------------------------
# application: student profiles
# --------------------------------------------------------------------------

class StudentProfile(Base, TimestampMixin):
    """What the application knows about one anonymous student.

    There is no user account: a profile is created for a session and referenced
    by its UUID. Authentication is out of scope.

    `experience_level` is nullable because "unknown" is a real state that the
    recommendation engine treats as neutral. It is never guessed at.
    """

    __tablename__ = "student_profiles"
    __table_args__ = (
        CheckConstraint(
            f"experience_level IS NULL OR {_one_of('experience_level', EXPERIENCE_LEVELS)}",
            name="ck_student_profiles_experience_level",
        ),
        CheckConstraint(
            f"experience_level_source IS NULL "
            f"OR {_one_of('experience_level_source', PROFILE_SOURCES)}",
            name="ck_student_profiles_experience_level_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    experience_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Nullable rather than defaulted, because it is only meaningful alongside a
    #: level: no level means nothing to attribute.
    experience_level_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    skills: Mapped[list[StudentProfileSkill]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    tags: Mapped[list[StudentProfileTag]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    entries: Mapped[list[StudentProfileEntry]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(back_populates="profile")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StudentProfile {self.id}>"


class StudentProfileSkill(Base):
    """A skill the student has, with proficiency in 0..1.

    An absent row means "not known to us", which the engine reads as 0. A stored
    0.0 means something different -- we asked and the answer was none -- so the
    two are never conflated.
    """

    __tablename__ = "student_profile_skills"
    __table_args__ = (
        CheckConstraint(
            "proficiency >= 0 AND proficiency <= 1",
            name="ck_student_profile_skills_proficiency_range",
        ),
        CheckConstraint(
            _one_of("source", PROFILE_SOURCES), name="ck_student_profile_skills_source"
        ),
        Index("ix_student_profile_skills_skill_id", "skill_id"),
    )

    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: RESTRICT on purpose: a skill still claimed by a student must not vanish
    #: because the taxonomy was edited. The seed fails loudly instead.
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="RESTRICT"), primary_key=True
    )
    proficiency: Mapped[float] = mapped_column(Float, nullable=False)
    #: Defaults to 'explicit' so rows written before this column existed, and
    #: rows written by hand or by a test fixture, keep the stronger meaning.
    #: Only the conversation layer records inference.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="explicit", default="explicit"
    )

    profile: Mapped[StudentProfile] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship()


class StudentProfileTag(Base):
    """An interest or work preference, from the same closed vocabularies as careers.

    Sharing the vocabulary with `career_tags` is what makes the interest and
    preference match terms a plain set overlap.
    """

    __tablename__ = "student_profile_tags"
    __table_args__ = (
        CheckConstraint(_one_of("tag_type", TAG_TYPES), name="ck_student_profile_tags_type"),
        CheckConstraint(
            _one_of("source", PROFILE_SOURCES), name="ck_student_profile_tags_source"
        ),
    )

    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="explicit", default="explicit"
    )

    profile: Mapped[StudentProfile] = relationship(back_populates="tags")


class StudentProfileEntry(Base, TimestampMixin):
    """A free-text strength, dislike or goal.

    These have no controlled vocabulary, so they are stored as text and are not
    scored. The LLM uses them when explaining; the engine never reads them.
    """

    __tablename__ = "student_profile_entries"
    __table_args__ = (
        CheckConstraint(
            _one_of("entry_type", PROFILE_ENTRY_TYPES), name="ck_student_profile_entries_type"
        ),
        CheckConstraint(
            _one_of("source", PROFILE_SOURCES), name="ck_student_profile_entries_source"
        ),
        Index("ix_student_profile_entries_profile_type", "student_profile_id", "entry_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="explicit", default="explicit"
    )

    profile: Mapped[StudentProfile] = relationship(back_populates="entries")


# --------------------------------------------------------------------------
# application: conversations
# --------------------------------------------------------------------------

class Conversation(Base, TimestampMixin):
    """One anonymous counselling session, addressed by UUID.

    The profile link is nullable so a conversation can start before there is
    anything worth recording about the student, and it is not unique so a
    returning session can hold several conversations against one profile.
    """

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_student_profile_id", "student_profile_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )

    profile: Mapped[StudentProfile | None] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Conversation {self.id}>"


class Message(Base):
    """One turn in a conversation.

    The primary key is a plain increasing integer rather than a UUID, so ordering
    by id is a total order over the whole conversation. Timestamps can collide;
    insertion order cannot.
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_one_of("role", MESSAGE_ROLES), name="ck_messages_role"),
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Message {self.id} {self.role}>"
