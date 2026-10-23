"""Add route_id to interval_actuals

Revision ID: zc3d4e5f6g7h
Revises: 809229190c87
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "zc3d4e5f6g7h"
down_revision = "809229190c87"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("interval_actuals",
                  sa.Column("route_id", sa.String(50), nullable=True))
    op.create_index("ix_actual_route_ts", "interval_actuals",
                    ["route_id", "timestamp"])


def downgrade():
    op.drop_index("ix_actual_route_ts", table_name="interval_actuals")
    op.drop_column("interval_actuals", "route_id")
