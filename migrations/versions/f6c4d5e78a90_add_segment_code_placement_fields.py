"""add segment code placement fields (offset, duration, window)

Revision ID: f6c4d5e78a90
Revises: e5b3c4d67f89
Create Date: 2026-09-28 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "f6c4d5e78a90"
down_revision = "e5b3c4d67f89"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent: skip columns that already exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("segment_codes")}

    if "offset_mins" not in existing:
        op.add_column("segment_codes", sa.Column("offset_mins", sa.Integer(), nullable=True))
    if "duration_mins" not in existing:
        op.add_column("segment_codes", sa.Column("duration_mins", sa.Integer(), nullable=True))
    if "is_flexible" not in existing:
        op.add_column("segment_codes", sa.Column("is_flexible", sa.Boolean(), server_default="0", nullable=True))
    if "window_start_mins" not in existing:
        op.add_column("segment_codes", sa.Column("window_start_mins", sa.Integer(), nullable=True))
    if "window_end_mins" not in existing:
        op.add_column("segment_codes", sa.Column("window_end_mins", sa.Integer(), nullable=True))

    # Seed defaults for break and lunch codes
    op.execute("""
        UPDATE segment_codes SET
            offset_mins = 120, duration_mins = 15,
            is_flexible = true, window_start_mins = 90, window_end_mins = 150
        WHERE code = 'break' AND offset_mins IS NULL
    """)
    op.execute("""
        UPDATE segment_codes SET
            offset_mins = 240, duration_mins = 30,
            is_flexible = true, window_start_mins = 210, window_end_mins = 300
        WHERE code = 'lunch' AND offset_mins IS NULL
    """)


def downgrade():
    op.drop_column("segment_codes", "window_end_mins")
    op.drop_column("segment_codes", "window_start_mins")
    op.drop_column("segment_codes", "is_flexible")
    op.drop_column("segment_codes", "duration_mins")
    op.drop_column("segment_codes", "offset_mins")
