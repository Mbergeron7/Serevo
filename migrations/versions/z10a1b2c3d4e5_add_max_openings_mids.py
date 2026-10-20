"""Add max_openings_per_week and max_mids_per_week to roster_members

Revision ID: z10a1b2c3d4e5
Revises: z9j0k1l2m3n4
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'z10a1b2c3d4e5'
down_revision = 'z9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_openings_per_week', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('max_mids_per_week', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.drop_column('max_mids_per_week')
        batch_op.drop_column('max_openings_per_week')
