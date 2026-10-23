"""Backfill route_id on existing IntervalActual rows

For planning units with exactly ONE assigned route, all their existing
NULL-route_id rows clearly came from that single route, so we can
safely backfill them.  PUs with 0 or 2+ routes are left as-is.

Revision ID: zd4e5f6g7h8i
Revises: zc3d4e5f6g7h
Create Date: 2026-10-23
"""
from alembic import op
import sqlalchemy as sa

revision = "zd4e5f6g7h8i"
down_revision = "zc3d4e5f6g7h"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Find PUs that have exactly one active route
    single_route_pus = conn.execute(sa.text("""
        SELECT planning_unit_id, MIN(route_id) AS route_id
        FROM call_routes
        WHERE is_active = 1 OR is_active = true
        GROUP BY planning_unit_id
        HAVING COUNT(*) = 1
    """)).fetchall()

    for pu_id, route_id in single_route_pus:
        conn.execute(sa.text("""
            UPDATE interval_actuals
            SET route_id = :rid
            WHERE planning_unit_id = :pu_id
              AND route_id IS NULL
        """), {"rid": route_id, "pu_id": pu_id})


def downgrade():
    # Can't undo — we don't know which rows were NULL before
    pass
