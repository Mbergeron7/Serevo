"""Add max_exception_days column to week_time_patterns

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='week_time_patterns' AND column_name='max_exception_days'"
    ))
    if not result.fetchone():
        op.add_column("week_time_patterns",
                       sa.Column("max_exception_days", sa.Integer(), nullable=True, server_default="0"))


def downgrade():
    op.drop_column("week_time_patterns", "max_exception_days")
