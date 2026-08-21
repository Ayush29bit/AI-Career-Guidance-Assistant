"""record how each profile fact reached us

The conversation layer distinguishes what a student stated outright from what
was inferred from what they said, so that a weak inference can never overwrite
an explicit statement. That distinction has to survive a request, so it lives in
the database rather than in memory.

Four additive columns, no data migration, no table rewrite:

* student_profile_skills.source          NOT NULL DEFAULT 'explicit'
* student_profile_tags.source            NOT NULL DEFAULT 'explicit'
* student_profile_entries.source         NOT NULL DEFAULT 'explicit'
* student_profiles.experience_level_source   NULL

The default is 'explicit' because every row written before this revision came
from a direct statement or a fixture -- nothing in the system inferred anything
yet -- so backfilling with the stronger value is the accurate answer, not a
convenient one. The experience column is nullable instead: a source is only
meaningful next to a level, and an unknown level has nothing to attribute.

The recommendation engine never reads any of these columns. Scoring is
unchanged by this revision.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Repeated from app.database.models.PROFILE_SOURCES, because a migration must
#: never import application code. This vocabulary is defined in Python, not in
#: the YAML knowledge base, so there is nothing for the seed to cross-check --
#: `alembic check` catches drift instead.
_PROFILE_SOURCES = "'explicit', 'inferred'"

#: Tables carrying a plain NOT NULL source column.
_SOURCED_TABLES = (
    "student_profile_skills",
    "student_profile_tags",
    "student_profile_entries",
)


def upgrade() -> None:
    for table in _SOURCED_TABLES:
        op.add_column(
            table,
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default="explicit",
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_source", table, f"source IN ({_PROFILE_SOURCES})"
        )

    op.add_column(
        "student_profiles",
        sa.Column("experience_level_source", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "ck_student_profiles_experience_level_source",
        "student_profiles",
        f"experience_level_source IS NULL OR experience_level_source IN ({_PROFILE_SOURCES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_student_profiles_experience_level_source", "student_profiles", type_="check"
    )
    op.drop_column("student_profiles", "experience_level_source")

    for table in reversed(_SOURCED_TABLES):
        op.drop_constraint(f"ck_{table}_source", table, type_="check")
        op.drop_column(table, "source")
