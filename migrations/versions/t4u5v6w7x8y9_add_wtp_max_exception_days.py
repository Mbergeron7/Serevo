"""Add missing columns to week_time_patterns

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


def _col_exists(bind, table, column):
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=:t AND column_name=:c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade():
    bind = op.get_bind()

    if not _col_exists(bind, "week_time_patterns", "max_exception_days"):
        op.add_column("week_time_patterns",
                       sa.Column("max_exception_days", sa.Integer(), nullable=True, server_default="0"))

    if not _col_exists(bind, "week_time_patterns", "planning_unit_id"):
        op.add_column("week_time_patterns",
                       sa.Column("planning_unit_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_wtp_planning_unit", "week_time_patterns",
                              "planning_units", ["planning_unit_id"], ["id"])

    if not _col_exists(bind, "week_time_patterns", "is_active"):
        op.add_column("week_time_patterns",
                       sa.Column("is_active", sa.Boolean(), nullable=True, server_default="true"))

    if not _col_exists(bind, "week_time_patterns", "created_at"):
        op.add_column("week_time_patterns",
                       sa.Column("created_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("week_time_patterns", "created_at")
    op.drop_column("week_time_patterns", "is_active")
    op.drop_constraint("fk_wtp_planning_unit", "week_time_patterns", type_="foreignkey")
    op.drop_column("week_time_patterns", "planning_unit_id")
    op.drop_column("week_time_patterns", "max_exception_days")
