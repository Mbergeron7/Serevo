"""Fix call_route unique constraint to be per planning unit

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-10-07
"""
from alembic import op

revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old global unique constraint on route_id alone (if it exists)
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {c["name"] for c in insp.get_unique_constraints("call_routes")}
    if "uq_call_route_id" in existing:
        op.drop_constraint("uq_call_route_id", "call_routes", type_="unique")
    # Add new composite unique constraint
    if "uq_call_route_pu" not in existing:
        op.create_unique_constraint("uq_call_route_pu", "call_routes",
                                    ["planning_unit_id", "route_id"])


def downgrade():
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    existing = {c["name"] for c in insp.get_unique_constraints("call_routes")}
    if "uq_call_route_pu" in existing:
        op.drop_constraint("uq_call_route_pu", "call_routes", type_="unique")
    if "uq_call_route_id" not in existing:
        op.create_unique_constraint("uq_call_route_id", "call_routes", ["route_id"])
