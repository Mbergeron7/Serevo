"""add rotation tables

Revision ID: b7e2a1c34f90
Revises: a8c3e2f01d5b
Create Date: 2026-09-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2a1c34f90'
down_revision = 'a8c3e2f01d5b'
branch_labels = None
depends_on = None


def _table_exists(name):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def upgrade():
    if not _table_exists('rotation_patterns'):
        op.create_table('rotation_patterns',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('weeks_json', sa.Text(), nullable=False, server_default='[]'),
            sa.Column('cycle_weeks', sa.Integer(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if not _table_exists('rotation_assignments'):
        op.create_table('rotation_assignments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('rotation_id', sa.Integer(), nullable=False),
            sa.Column('employee_id', sa.Integer(), nullable=False),
            sa.Column('current_week', sa.Integer(), nullable=True),
            sa.Column('start_date', sa.Date(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['rotation_id'], ['rotation_patterns.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['employee_id'], ['employees.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('rotation_id', 'employee_id', name='uq_rotation_employee'),
        )
        op.create_index('ix_rotation_assignments_rotation_id', 'rotation_assignments', ['rotation_id'])
        op.create_index('ix_rotation_assignments_employee_id', 'rotation_assignments', ['employee_id'])


def downgrade():
    op.drop_index('ix_rotation_assignments_employee_id', table_name='rotation_assignments')
    op.drop_index('ix_rotation_assignments_rotation_id', table_name='rotation_assignments')
    op.drop_table('rotation_assignments')
    op.drop_table('rotation_patterns')
