"""Add roster rotation module tables and source column on schedules

Revision ID: z3d4e5f6g7h8
Revises: z2c3d4e5f6g7
Create Date: 2026-10-14 20:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'z3d4e5f6g7h8'
down_revision = 'z2c3d4e5f6g7'
branch_labels = None
depends_on = None


def _table_exists(name):
    """Check if a table already exists (handles both Postgres and SQLite)."""
    from alembic import op as _op
    bind = _op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _column_exists(table, column):
    """Check if a column already exists on a table."""
    from alembic import op as _op
    bind = _op.get_bind()
    insp = sa.inspect(bind)
    return any(c['name'] == column for c in insp.get_columns(table))


def upgrade():
    # Add source column to schedules (skip if already added by db.create_all)
    if not _column_exists('schedules', 'source'):
        op.add_column('schedules', sa.Column('source', sa.String(20), nullable=True))

    # RosterGroup
    if not _table_exists('roster_groups'):
        op.create_table('roster_groups',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('planning_unit_id', sa.Integer, sa.ForeignKey('planning_units.id'), nullable=False, index=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('closers_required_weekday', sa.Integer, server_default='1'),
        sa.Column('closers_required_weekend', sa.Integer, server_default='1'),
        sa.Column('weekend_mode', sa.String(20), server_default='rotate'),
        sa.Column('stat_role', sa.String(20), nullable=True),
        sa.Column('active', sa.Boolean, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime),
        sa.Column('updated_at', sa.DateTime),
    )

    # RosterMember
    if not _table_exists('roster_members'):
        op.create_table('roster_members',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('roster_group_id', sa.Integer, sa.ForeignKey('roster_groups.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('employee_id', sa.Integer, sa.ForeignKey('employees.id'), nullable=False, index=True),
        sa.Column('is_display_only', sa.Boolean, server_default=sa.text('false')),
        sa.Column('is_weekend_primary', sa.Boolean, server_default=sa.text('false')),
        sa.Column('weekend_primary_days', sa.Text, nullable=True),
        sa.Column('max_closings_per_week', sa.Integer, server_default='1'),
        sa.Column('excluded_closing_days', sa.Text, nullable=True),
        sa.Column('static_days_json', sa.Text, nullable=True),
        sa.Column('accommodation_start', sa.Time, nullable=True),
        sa.Column('accommodation_end', sa.Time, nullable=True),
        sa.Column('accommodation_no_close', sa.Boolean, server_default=sa.text('false')),
        sa.Column('work_days', sa.Text, nullable=True),
        sa.Column('active', sa.Boolean, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime),
        sa.UniqueConstraint('roster_group_id', 'employee_id', name='uq_roster_member'),
    )

    # RosterSlot
    if not _table_exists('roster_slots'):
        op.create_table('roster_slots',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('roster_group_id', sa.Integer, sa.ForeignKey('roster_groups.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('day_type', sa.String(20), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('shift_start', sa.Time, nullable=False),
        sa.Column('shift_end', sa.Time, nullable=False),
        sa.UniqueConstraint('roster_group_id', 'day_type', 'role', name='uq_roster_slot'),
    )

    # RosterRotationState
    if not _table_exists('roster_rotation_states'):
        op.create_table('roster_rotation_states',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('roster_member_id', sa.Integer, sa.ForeignKey('roster_members.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('period_start', sa.Date, nullable=False),
        sa.Column('weekday_close_count', sa.Integer, server_default='0'),
        sa.Column('weekend_count', sa.Integer, server_default='0'),
        sa.Column('stat_count', sa.Integer, server_default='0'),
        sa.Column('last_close_weekday', sa.Integer, nullable=True),
        sa.Column('last_close_date', sa.Date, nullable=True),
        sa.Column('last_weekend_date', sa.Date, nullable=True),
        sa.Column('weekday_rotation_index', sa.Integer, server_default='0'),
        sa.Column('updated_at', sa.DateTime),
        sa.UniqueConstraint('roster_member_id', 'period_start', name='uq_roster_state'),
    )


def downgrade():
    op.drop_table('roster_rotation_states')
    op.drop_table('roster_slots')
    op.drop_table('roster_members')
    op.drop_table('roster_groups')
    op.drop_column('schedules', 'source')
