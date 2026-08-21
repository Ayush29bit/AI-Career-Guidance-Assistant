"""PostgreSQL persistence layer.

Holds the SQLAlchemy models, engine/session management and the knowledge seed.
The deterministic recommendation engine does not import anything from here: it
keeps reading the in-memory knowledge base and course catalogue, so scoring stays
reproducible and testable without a database.
"""

from app.database.models import (
    Base,
    Career,
    CareerSkill,
    CareerTag,
    Conversation,
    Course,
    CourseSkill,
    Message,
    Skill,
    SkillPrerequisite,
    SkillRelation,
    StudentProfile,
    StudentProfileEntry,
    StudentProfileSkill,
    StudentProfileTag,
)
from app.database.session import get_engine, get_session, get_sessionmaker, session_scope

__all__ = [
    "Base",
    "Career",
    "CareerSkill",
    "CareerTag",
    "Conversation",
    "Course",
    "CourseSkill",
    "Message",
    "Skill",
    "SkillPrerequisite",
    "SkillRelation",
    "StudentProfile",
    "StudentProfileEntry",
    "StudentProfileSkill",
    "StudentProfileTag",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "session_scope",
]
