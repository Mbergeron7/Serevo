"""Merge Activity table into SegmentCode.

Activity rows are copied into segment_codes (matching by name→label),
DayModelActivity FK is updated, then the activities table is dropped.

Revision ID: zk1l2m3n4o5p
Revises: zj0k1l2m3n4o
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zk1l2m3n4o5p"
down_revision = "zj0k1l2m3n4o"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Check if activities table exists (it may not on fresh installs)
    inspector = sa.inspect(conn)
    if "activities" not in inspector.get_table_names():
        return

    # 2. Copy any Activity rows into segment_codes that don't already exist
    #    (match by label/name)
    # Detect which columns actually exist on the activities table
    existing_cols = {c["name"] for c in inspector.get_columns("activities")}
    base_cols = ["id", "name", "short_name", "activity_type", "is_paid", "is_productive",
                 "duration_mins", "color", "segment_code_id", "is_active", "sort_order"]
    optional_cols = ["activity_category", "external_ids", "parent_id", "is_multi_activity"]
    select_cols = base_cols + [c for c in optional_cols if c in existing_cols]
    activities = conn.execute(sa.text(
        f"SELECT {', '.join(select_cols)} FROM activities"
    )).fetchall()

    def _get(row, col, default=None):
        """Safely get a column value that may not exist in the result set."""
        try:
            return getattr(row, col, default)
        except Exception:
            return default

    activity_id_to_sc_id = {}
    for act in activities:
        # If the activity has a segment_code_id, use that mapping directly
        if _get(act, "segment_code_id"):
            activity_id_to_sc_id[act.id] = act.segment_code_id
        else:
            # Check if a segment_code with the same label exists
            existing = conn.execute(sa.text(
                "SELECT id FROM segment_codes WHERE label = :label"
            ), {"label": act.name}).fetchone()
            if existing:
                activity_id_to_sc_id[act.id] = existing.id
            else:
                # Insert a new segment_code from this activity
                code_slug = act.name.lower().replace(" ", "_").replace("-", "_")[:40]
                # Ensure code is unique
                dup = conn.execute(sa.text(
                    "SELECT id FROM segment_codes WHERE code = :code"
                ), {"code": code_slug}).fetchone()
                suffix = 1
                orig_slug = code_slug
                while dup:
                    code_slug = f"{orig_slug[:36]}_{suffix}"
                    dup = conn.execute(sa.text(
                        "SELECT id FROM segment_codes WHERE code = :code"
                    ), {"code": code_slug}).fetchone()
                    suffix += 1

                conn.execute(sa.text(
                    "INSERT INTO segment_codes (code, label, color, is_productive, is_paid, "
                    "is_active, sort_order, activity_type, activity_category, abbreviation, "
                    "external_ids, is_multi_activity, duration_mins) "
                    "VALUES (:code, :label, :color, :is_productive, :is_paid, "
                    ":is_active, :sort_order, :activity_type, :activity_category, :abbreviation, "
                    ":external_ids, :is_multi_activity, :duration_mins)"
                ), {
                    "code": code_slug, "label": act.name,
                    "color": _get(act, "color") or "#6b7280",
                    "is_productive": _get(act, "is_productive", True),
                    "is_paid": _get(act, "is_paid", True),
                    "is_active": _get(act, "is_active", True),
                    "sort_order": _get(act, "sort_order", 0) or 0,
                    "activity_type": _get(act, "activity_type") or "presence",
                    "activity_category": _get(act, "activity_category") or "status",
                    "abbreviation": _get(act, "short_name") or "",
                    "external_ids": _get(act, "external_ids"),
                    "is_multi_activity": _get(act, "is_multi_activity", False) or False,
                    "duration_mins": _get(act, "duration_mins"),
                })
                new_row = conn.execute(sa.text(
                    "SELECT id FROM segment_codes WHERE code = :code"
                ), {"code": code_slug}).fetchone()
                activity_id_to_sc_id[act.id] = new_row.id

    # 3. Update DayModelActivity FK values
    if "day_model_activities" in inspector.get_table_names():
        for old_id, new_id in activity_id_to_sc_id.items():
            conn.execute(sa.text(
                "UPDATE day_model_activities SET activity_id = :new_id "
                "WHERE activity_id = :old_id"
            ), {"new_id": new_id, "old_id": old_id})

    # 4. Drop FK constraints that reference activities before dropping it
    if "day_model_activities" in inspector.get_table_names():
        # Drop the FK from day_model_activities -> activities
        try:
            op.drop_constraint("day_model_activities_activity_id_fkey", "day_model_activities", type_="foreignkey")
        except Exception:
            pass  # Constraint may not exist or have a different name
        # Re-point FK to segment_codes
        try:
            op.create_foreign_key(
                "day_model_activities_activity_id_fkey",
                "day_model_activities", "segment_codes",
                ["activity_id"], ["id"]
            )
        except Exception:
            pass  # May already exist

    # 5. Drop the activities table
    op.drop_table("activities")


def downgrade():
    # Recreate the activities table (empty — data migration is one-way)
    op.create_table(
        "activities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("short_name", sa.String(20), default=""),
        sa.Column("activity_type", sa.String(30), default="presence"),
        sa.Column("is_paid", sa.Boolean, default=True),
        sa.Column("is_productive", sa.Boolean, default=True),
        sa.Column("duration_mins", sa.Integer, nullable=True),
        sa.Column("color", sa.String(20), default="#6b7280"),
        sa.Column("segment_code_id", sa.Integer, sa.ForeignKey("segment_codes.id"), nullable=True),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("sort_order", sa.Integer, default=0),
        sa.Column("activity_category", sa.String(20), default="status"),
        sa.Column("external_ids", sa.Text, nullable=True),
        sa.Column("parent_id", sa.Integer, nullable=True),
        sa.Column("is_multi_activity", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime),
    )
