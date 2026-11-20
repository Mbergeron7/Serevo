"""Allow NULL employee_id on schedules for unassigned bidding slots.

Revision ID: zf7g8h9i0j1k
Revises: ze6f7g8h9i0j
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'zf7g8h9i0j1k'
down_revision = 'ze6f7g8h9i0j'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('schedules', 'employee_id',
                     existing_type=sa.Integer(),
                     nullable=True)


def downgrade():
    # Set any NULLs to 0 before making non-nullable (or delete them)
    op.execute("DELETE FROM schedules WHERE employee_id IS NULL")
    op.alter_column('schedules', 'employee_id',
                     existing_type=sa.Integer(),
                     nullable=False)
