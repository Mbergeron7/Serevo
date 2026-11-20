"""Add bidding_windows and shift_preferences tables for shift bidding cycles.

Revision ID: ze6f7g8h9i0j
Revises: zo5p6q7r8s9t
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'ze6f7g8h9i0j'
down_revision = 'zd5e6f7g8h9i'
branch_labels = None
depends_on = None


def _has_table(name):
    conn = op.get_bind()
    return sa.inspect(conn).has_table(name)


def upgrade():
    if not _has_table('bidding_windows'):
        op.create_table(
            'bidding_windows',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('planning_unit_id', sa.Integer(), sa.ForeignKey('planning_units.id'), nullable=True),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date(), nullable=False),
            sa.Column('deadline', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(20), server_default='draft'),
            sa.Column('rotation_seed', sa.Integer(), server_default='0'),
            sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('created_at', sa.DateTime()),
            sa.Column('updated_at', sa.DateTime()),
        )

    if not _has_table('shift_preferences'):
        op.create_table(
            'shift_preferences',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('bidding_window_id', sa.Integer(), sa.ForeignKey('bidding_windows.id', ondelete='CASCADE'), nullable=False),
            sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False),
            sa.Column('schedule_id', sa.Integer(), sa.ForeignKey('schedules.id', ondelete='CASCADE'), nullable=False),
            sa.Column('preference_rank', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(20), server_default='pending'),
            sa.Column('created_at', sa.DateTime()),
            sa.UniqueConstraint('bidding_window_id', 'employee_id', 'schedule_id', name='uq_bid_emp_sched'),
        )


def downgrade():
    if _has_table('shift_preferences'):
        op.drop_table('shift_preferences')
    if _has_table('bidding_windows'):
        op.drop_table('bidding_windows')
