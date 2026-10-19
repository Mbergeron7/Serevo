"""Add time_clock table for employee punch-in/out

Revision ID: z5f6g7h8i9j0
Revises: z4e5f6g7h8i9
Create Date: 2026-10-19 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'z5f6g7h8i9j0'
down_revision = 'z4e5f6g7h8i9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'time_clock',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.id'), nullable=False, index=True),
        sa.Column('clock_in', sa.DateTime(), nullable=False),
        sa.Column('clock_out', sa.DateTime(), nullable=True),
        sa.Column('clock_in_note', sa.String(200), nullable=True),
        sa.Column('clock_out_note', sa.String(200), nullable=True),
        sa.Column('status', sa.String(20), server_default='active'),
        sa.Column('total_hours', sa.Float(), nullable=True),
        sa.Column('date', sa.Date(), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('edited_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('edited_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('time_clock')
