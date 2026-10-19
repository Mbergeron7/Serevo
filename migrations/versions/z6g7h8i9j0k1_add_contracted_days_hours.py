"""Add contracted_days and contracted_hours to roster_members

Revision ID: z6g7h8i9j0k1
Revises: z5f6g7h8i9j0
Create Date: 2026-10-19 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'z6g7h8i9j0k1'
down_revision = '16cf8c560340'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contracted_days', sa.Integer(), server_default='5'))
        batch_op.add_column(sa.Column('contracted_hours', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.drop_column('contracted_hours')
        batch_op.drop_column('contracted_days')
