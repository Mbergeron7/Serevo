"""Add WFM ticket portal-style fields

Revision ID: zb2c3d4e5f6g
Revises: za1b2c3d4e5f
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'zb2c3d4e5f6g'
down_revision = 'za1b2c3d4e5f'
branch_labels = None
depends_on = None


def _has_column(table, column):
    """Check if a column exists in a table."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    try:
        cols = [c['name'] for c in insp.get_columns(table)]
        return column in cols
    except Exception:
        return False


def _has_table(table):
    """Check if a table exists."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return table in insp.get_table_names()


def upgrade():
    if not _has_table('wfm_ticket'):
        # Table will be created by db.create_all() with all columns
        return

    new_cols = [
        ('affected_date_end', sa.Date(), True),
        ('lob', sa.String(length=120), True),
        ('shift_time', sa.String(length=60), True),
        ('overtime_hours', sa.Float(), True),
        ('swap_with', sa.String(length=200), True),
    ]
    for col_name, col_type, nullable in new_cols:
        if not _has_column('wfm_ticket', col_name):
            op.add_column('wfm_ticket', sa.Column(col_name, col_type, nullable=nullable))


def downgrade():
    if not _has_table('wfm_ticket'):
        return
    for col_name in ('swap_with', 'overtime_hours', 'shift_time', 'lob', 'affected_date_end'):
        if _has_column('wfm_ticket', col_name):
            op.drop_column('wfm_ticket', col_name)
