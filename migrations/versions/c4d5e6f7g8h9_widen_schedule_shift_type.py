"""Widen schedules.shift_type from 10 to 60 chars

Revision ID: c4d5e6f7g8h9
Revises: b3c4d5e6f7g8
Create Date: 2026-10-16 00:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d5e6f7g8h9'
down_revision = 'b3c4d5e6f7g8'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.alter_column('schedules', 'shift_type',
                        type_=sa.String(60), existing_type=sa.String(10))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.alter_column('schedules', 'shift_type',
                        type_=sa.String(10), existing_type=sa.String(60))
