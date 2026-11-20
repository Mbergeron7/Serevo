"""Add remaining WFM ticket columns missed by prior migration

Revision ID: zd5e6f7g8h9i
Revises: zc4d5e6f7g8h
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'zd5e6f7g8h9i'
down_revision = 'zc4d5e6f7g8h'
branch_labels = None
depends_on = None


def _has_column(table, column):
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    try:
        cols = [c['name'] for c in insp.get_columns(table)]
        return column in cols
    except Exception:
        return False


def upgrade():
    table = 'wfm_tickets'
    new_cols = [
        ('resolution', sa.Text(), True),
        ('internal_note', sa.Text(), True),
        ('closed_at', sa.DateTime(), True),
        ('closed_by', sa.Integer(), True),
        ('is_archived', sa.Boolean(), True),
        ('is_deleted', sa.Boolean(), True),
    ]
    for col_name, col_type, nullable in new_cols:
        if not _has_column(table, col_name):
            op.add_column(table, sa.Column(col_name, col_type, nullable=nullable))


def downgrade():
    table = 'wfm_tickets'
    for col_name in ['is_deleted', 'is_archived', 'closed_by', 'closed_at', 'internal_note', 'resolution']:
        if _has_column(table, col_name):
            op.drop_column(table, col_name)
