"""Add Organization model for multi-tenancy foundation

Revision ID: zj0k1l2m3n4o
Revises: zi9j0k1l2m3n
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zj0k1l2m3n4o"
down_revision = "zi9j0k1l2m3n"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    table_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'organizations')"
    )).scalar()

    if not table_exists:
        op.create_table(
            "organizations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(120), unique=True, nullable=False),
            sa.Column("slug", sa.String(60), unique=True, nullable=False),
            sa.Column("timezone", sa.String(60), server_default="America/Toronto"),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade():
    op.drop_table("organizations")
