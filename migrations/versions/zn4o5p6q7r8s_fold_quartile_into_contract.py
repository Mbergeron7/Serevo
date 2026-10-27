"""Item 15: Fold Quartile into Contract — drop QuartileScheduleRule, add shift columns.

Drop quartile_schedule_rules table (unused by engine).
Add allowed_shift_templates, max_weekends_per_period, scheduling_priority to contracts.

Revision ID: zn4o5p6q7r8s
Revises: zm3n4o5p6q7r
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zn4o5p6q7r8s"
down_revision = "zm3n4o5p6q7r"
branch_labels = None
depends_on = None


def _table_exists(table):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"
    ), {"t": table})
    return result.scalar()


def _column_exists(table, column):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c)"
    ), {"t": table, "c": column})
    return result.scalar()


def upgrade():
    # 1. Drop QuartileScheduleRule table (dead code — never used by engine)
    if _table_exists("quartile_schedule_rules"):
        op.drop_table("quartile_schedule_rules")

    # 2. Add new columns to contracts
    if not _column_exists("contracts", "allowed_shift_templates"):
        op.add_column("contracts", sa.Column("allowed_shift_templates", sa.Text(), nullable=True))
    if not _column_exists("contracts", "max_weekends_per_period"):
        op.add_column("contracts", sa.Column("max_weekends_per_period", sa.Integer(), nullable=True))
    if not _column_exists("contracts", "scheduling_priority"):
        op.add_column("contracts", sa.Column("scheduling_priority", sa.Integer(), nullable=True))


def downgrade():
    # Remove added columns
    if _column_exists("contracts", "scheduling_priority"):
        op.drop_column("contracts", "scheduling_priority")
    if _column_exists("contracts", "max_weekends_per_period"):
        op.drop_column("contracts", "max_weekends_per_period")
    if _column_exists("contracts", "allowed_shift_templates"):
        op.drop_column("contracts", "allowed_shift_templates")

    # Recreate QuartileScheduleRule table
    if not _table_exists("quartile_schedule_rules"):
        op.create_table(
            "quartile_schedule_rules",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("planning_unit_id", sa.Integer,
                      sa.ForeignKey("planning_units.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("quartile", sa.Integer, nullable=False),
            sa.Column("day_model_id", sa.Integer,
                      sa.ForeignKey("day_models.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("priority", sa.Integer, server_default="0"),
            sa.Column("created_at", sa.DateTime),
            sa.UniqueConstraint("planning_unit_id", "quartile", "day_model_id",
                                name="uq_quartile_rule"),
        )
