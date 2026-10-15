"""Add attendance_events table

Revision ID: a1b2c3d4e5f6
Revises: 2a52fe3d286f
Create Date: 2026-10-15 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '2a52fe3d286f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'attendance_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False, index=True),
        sa.Column('event_date', sa.Date(), nullable=False, index=True),
        sa.Column('adherence_exception_id', sa.Integer(), sa.ForeignKey('adherence_exceptions.id'), nullable=False),
        sa.Column('note', sa.Text(), default=''),
        sa.Column('is_excused', sa.Boolean(), default=False),
        sa.Column('coded_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
    )


def downgrade():
    op.drop_table('attendance_events')
