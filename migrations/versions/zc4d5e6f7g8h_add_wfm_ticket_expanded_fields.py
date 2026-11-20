"""Add expanded WFM ticket fields (14 new columns)

Revision ID: zc4d5e6f7g8h
Revises: 471aad42f325
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'zc4d5e6f7g8h'
down_revision = '471aad42f325'
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
        ('affected_date_end', sa.Date(), True),
        ('lob', sa.String(120), True),
        ('shift_time', sa.String(60), True),
        ('overtime_hours', sa.Float(), True),
        ('swap_with', sa.String(200), True),
        ('action_type', sa.String(20), True),
        ('accommodation_type', sa.String(30), True),
        ('has_documentation', sa.Boolean(), True),
        ('day_times', sa.Text(), True),
        ('contracted_hours', sa.Float(), True),
        ('current_start', sa.String(10), True),
        ('current_end', sa.String(10), True),
        ('new_start', sa.String(10), True),
        ('new_end', sa.String(10), True),
        ('activity_name', sa.String(200), True),
        ('skill_name', sa.String(100), True),
        ('status_change', sa.String(100), True),
        ('date_mode', sa.String(10), True),
    ]
    for col_name, col_type, nullable in new_cols:
        if not _has_column(table, col_name):
            op.add_column(table, sa.Column(col_name, col_type, nullable=nullable))


def downgrade():
    table = 'wfm_tickets'
    for col_name in reversed([
        'affected_date_end', 'lob', 'shift_time', 'overtime_hours', 'swap_with',
        'action_type', 'accommodation_type', 'has_documentation', 'day_times',
        'contracted_hours', 'current_start', 'current_end', 'new_start', 'new_end',
        'activity_name', 'skill_name', 'status_change', 'date_mode',
    ]):
        if _has_column(table, col_name):
            op.drop_column(table, col_name)
