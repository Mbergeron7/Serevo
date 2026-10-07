"""Add abbreviation column to week_time_patterns

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='week_time_patterns' AND column_name='abbreviation'"
    ))
    if not result.fetchone():
        op.add_column("week_time_patterns",
                       sa.Column("abbreviation", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("week_time_patterns", "abbreviation")
