"""Add day_model_activities junction table

Revision ID: zi9j0k1l2m3n
Revises: zh8i9j0k1l2m
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "zi9j0k1l2m3n"
down_revision = "zh8i9j0k1l2m"
branch_labels = None
depends_on = None


def upgrade():
    # Use IF NOT EXISTS to handle the case where the table was already created
    conn = op.get_bind()
    table_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'day_model_activities')"
    )).scalar()

    if not table_exists:
        op.create_table(
            "day_model_activities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("day_model_id", sa.Integer(), sa.ForeignKey("day_models.id", ondelete="CASCADE"), nullable=False),
            sa.Column("activity_id", sa.Integer(), sa.ForeignKey("activities.id"), nullable=True),
            sa.Column("offset_mins", sa.Integer(), default=0),
            sa.Column("duration_mins", sa.Integer(), default=30),
            sa.Column("is_flexible", sa.Boolean(), default=False),
            sa.Column("window_start", sa.Integer(), nullable=True),
            sa.Column("window_end", sa.Integer(), nullable=True),
            sa.Column("sort_order", sa.Integer(), default=0),
        )
        op.create_index("ix_day_model_activities_day_model_id", "day_model_activities", ["day_model_id"])
        op.create_index("ix_day_model_activities_activity_id", "day_model_activities", ["activity_id"])

    # Backfill from activities_json
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, activities_json FROM day_models WHERE activities_json IS NOT NULL AND activities_json != '[]'"))
    for row in rows:
        try:
            acts = json.loads(row[1]) if row[1] else []
        except (json.JSONDecodeError, TypeError):
            continue
        for i, act in enumerate(acts):
            conn.execute(sa.text("""
                INSERT INTO day_model_activities
                    (day_model_id, activity_id, offset_mins, duration_mins, is_flexible, window_start, window_end, sort_order)
                VALUES (:dm_id, :act_id, :offset, :dur, :flex, :ws, :we, :so)
            """), {
                "dm_id": row[0],
                "act_id": act.get("activity_id"),
                "offset": act.get("offset_mins", 0),
                "dur": act.get("duration_mins", 30),
                "flex": act.get("is_flexible", False),
                "ws": act.get("window_start"),
                "we": act.get("window_end"),
                "so": i,
            })


def downgrade():
    op.drop_table("day_model_activities")
