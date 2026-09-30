"""add team_lead to employees

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("employees", sa.Column("team_lead", sa.String(100), server_default="", nullable=True))


def downgrade():
    op.drop_column("employees", "team_lead")
