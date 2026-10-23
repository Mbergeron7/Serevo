"""Add composite indexes on forecast_intervals and requirement_intervals

Revision ID: zg7h8i9j0k1l
Revises: zf6g7h8i9j0k
Create Date: 2026-10-23
"""
from alembic import op

revision = "zg7h8i9j0k1l"
down_revision = "zf6g7h8i9j0k"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_forecast_pu_ts", "forecast_intervals", ["planning_unit_id", "timestamp"])
    op.create_index("ix_requirement_pu_ts", "requirement_intervals", ["planning_unit_id", "timestamp"])


def downgrade():
    op.drop_index("ix_requirement_pu_ts", table_name="requirement_intervals")
    op.drop_index("ix_forecast_pu_ts", table_name="forecast_intervals")
