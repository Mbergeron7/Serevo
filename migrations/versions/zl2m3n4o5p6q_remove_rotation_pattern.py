"""Remove RotationPattern and RotationAssignment tables (Item 13).

RotationPattern functionality is folded into ShiftSequence.

Revision ID: zl2m3n4o5p6q
Revises: zk1l2m3n4o5p
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zl2m3n4o5p6q"
down_revision = "zk1l2m3n4o5p"
branch_labels = None
depends_on = None


def _table_exists(table):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"
    ), {"t": table})
    return result.scalar()


def upgrade():
    if _table_exists("rotation_assignments"):
        op.drop_table("rotation_assignments")
    if _table_exists("rotation_patterns"):
        op.drop_table("rotation_patterns")


def downgrade():
    op.create_table(
        "rotation_patterns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("weeks_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("cycle_weeks", sa.Integer, server_default="1"),
        sa.Column("is_active", sa.Boolean, server_default="1"),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "rotation_assignments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rotation_id", sa.Integer, sa.ForeignKey("rotation_patterns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.Integer, sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("current_week", sa.Integer, server_default="0"),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )
