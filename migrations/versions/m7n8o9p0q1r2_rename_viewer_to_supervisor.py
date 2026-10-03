"""Rename viewer role to supervisor

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-10-02
"""
from alembic import op

# revision identifiers
revision = 'm7n8o9p0q1r2'
down_revision = 'l6m7n8o9p0q1'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE users SET role = 'supervisor' WHERE role = 'viewer'")


def downgrade():
    op.execute("UPDATE users SET role = 'viewer' WHERE role = 'supervisor'")
