"""Add special_days table

Revision ID: 471aad42f325
Revises: zo5p6q7r8s9t
Create Date: 2026-10-28 09:45:45.093529

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '471aad42f325'
down_revision = 'zo5p6q7r8s9t'
branch_labels = None
depends_on = None


def upgrade():
    # Create special_days table (skip if already exists from create_all)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = inspector.get_table_names()

    if "special_days" not in existing:
        op.create_table(
            "special_days",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("date", sa.Date(), nullable=False, index=True),
            sa.Column("day_type", sa.String(20), nullable=False, server_default="holiday"),
            sa.Column("color", sa.String(10), server_default="#ef4444"),
            sa.Column("volume_factor", sa.Float(), server_default="0.0"),
            sa.Column("is_recurring", sa.Boolean(), server_default=sa.text("false")),
            sa.Column("applies_to_all", sa.Boolean(), server_default=sa.text("true")),
            sa.Column("notes", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("name", "date", name="uq_special_day_name_date"),
        )

    if "special_day_planning_units" not in existing:
        op.create_table(
            "special_day_planning_units",
            sa.Column("special_day_id", sa.Integer(),
                      sa.ForeignKey("special_days.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("planning_unit_id", sa.Integer(),
                      sa.ForeignKey("planning_units.id", ondelete="CASCADE"),
                      primary_key=True),
        )


def downgrade():
    op.drop_table("special_day_planning_units")
    op.drop_table("special_days")
