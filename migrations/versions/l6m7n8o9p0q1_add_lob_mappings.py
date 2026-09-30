"""add lob_mappings table

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-09-30 19:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade():
    # Guard: table may already exist from db.create_all()
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'lob_mappings'"
        )
    )
    if result.fetchone():
        return  # already exists, nothing to do

    op.create_table(
        "lob_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(200), nullable=False, unique=True, index=True),
        sa.Column("planning_unit_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("lob_mappings")
