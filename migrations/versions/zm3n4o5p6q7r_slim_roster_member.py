"""Slim RosterMember: remove legacy exclude_from_weekends column (Item 16).

Backfill exclude_from_saturday/sunday from exclude_from_weekends before dropping.

Revision ID: zm3n4o5p6q7r
Revises: zl2m3n4o5p6q
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zm3n4o5p6q7r"
down_revision = "zl2m3n4o5p6q"
branch_labels = None
depends_on = None


def _column_exists(table, column):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c)"
    ), {"t": table, "c": column})
    return result.scalar()


def upgrade():
    if not _column_exists("roster_members", "exclude_from_weekends"):
        return

    conn = op.get_bind()
    # Backfill: if exclude_from_weekends=true, set both saturday and sunday to true
    conn.execute(sa.text(
        "UPDATE roster_members SET exclude_from_saturday = true, exclude_from_sunday = true "
        "WHERE exclude_from_weekends = true AND (exclude_from_saturday = false OR exclude_from_sunday = false)"
    ))

    op.drop_column("roster_members", "exclude_from_weekends")


def downgrade():
    if not _column_exists("roster_members", "exclude_from_weekends"):
        op.add_column("roster_members", sa.Column("exclude_from_weekends", sa.Boolean(), server_default="0"))
