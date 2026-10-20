"""Add consistent_start flag to roster_member

Revision ID: z8i9j0k1l2m3
Revises: z7h8i9j0k1l2
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'z8i9j0k1l2m3'
down_revision = 'z7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_member', schema=None) as batch_op:
        batch_op.add_column(sa.Column('consistent_start', sa.Boolean(), server_default=sa.false(), nullable=True))


def downgrade():
    with op.batch_alter_table('roster_member', schema=None) as batch_op:
        batch_op.drop_column('consistent_start')
