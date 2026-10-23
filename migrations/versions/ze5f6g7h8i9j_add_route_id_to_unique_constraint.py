"""Add route_id to IntervalActual unique constraint

The old constraint (planning_unit_id, timestamp) only allows one row
per PU per interval, so when multiple routes map to the same PU their
data overwrites each other.  The new constraint
(planning_unit_id, route_id, timestamp) lets each route store its own
row.

Revision ID: ze5f6g7h8i9j
Revises: zd4e5f6g7h8i
Create Date: 2026-10-23
"""
from alembic import op

revision = "ze5f6g7h8i9j"
down_revision = "zd4e5f6g7h8i"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_actual_unit_ts", "interval_actuals", type_="unique")
    op.create_unique_constraint(
        "uq_actual_unit_route_ts",
        "interval_actuals",
        ["planning_unit_id", "route_id", "timestamp"],
    )


def downgrade():
    op.drop_constraint("uq_actual_unit_route_ts", "interval_actuals", type_="unique")
    op.create_unique_constraint(
        "uq_actual_unit_ts",
        "interval_actuals",
        ["planning_unit_id", "timestamp"],
    )
