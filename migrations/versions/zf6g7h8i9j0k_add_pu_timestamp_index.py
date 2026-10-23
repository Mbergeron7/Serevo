"""Add composite index on planning_unit_id + timestamp for faster queries

Revision ID: zf6g7h8i9j0k
Revises: ze5f6g7h8i9j
Create Date: 2026-10-23
"""
from alembic import op

revision = "zf6g7h8i9j0k"
down_revision = "ze5f6g7h8i9j"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_actual_pu_ts", "interval_actuals", ["planning_unit_id", "timestamp"])


def downgrade():
    op.drop_index("ix_actual_pu_ts", table_name="interval_actuals")
