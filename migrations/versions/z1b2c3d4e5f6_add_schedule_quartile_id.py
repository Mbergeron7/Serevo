"""Add schedule_quartile_id to employee_quartiles

Revision ID: z1b2c3d4e5f6
Revises: z0a1b2c3d4e5
Create Date: 2026-10-13 16:50:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'z1b2c3d4e5f6'
down_revision = 'z0a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Check if column already exists (idempotent)
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'employee_quartiles' AND column_name = 'schedule_quartile_id'"
    ))
    if result.fetchone():
        return

    op.add_column('employee_quartiles',
                   sa.Column('schedule_quartile_id', sa.Integer,
                             sa.ForeignKey('week_time_patterns.id', ondelete='SET NULL'),
                             nullable=True))
    op.create_index('ix_employee_quartiles_schedule_quartile_id',
                    'employee_quartiles', ['schedule_quartile_id'])


def downgrade():
    op.drop_index('ix_employee_quartiles_schedule_quartile_id', 'employee_quartiles')
    op.drop_column('employee_quartiles', 'schedule_quartile_id')
