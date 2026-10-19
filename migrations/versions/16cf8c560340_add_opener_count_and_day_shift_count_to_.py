"""Add opener_count and day_shift_count to roster_rotation_states

Revision ID: 16cf8c560340
Revises: z5f6g7h8i9j0
Create Date: 2026-10-19 11:49:00.695904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '16cf8c560340'
down_revision = 'z5f6g7h8i9j0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.add_column(sa.Column('opener_count', sa.Integer(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('day_shift_count', sa.Integer(), nullable=True, server_default='0'))


def downgrade():
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.drop_column('day_shift_count')
        batch_op.drop_column('opener_count')
