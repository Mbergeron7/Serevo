"""Add rotation_order to roster_members

Revision ID: 2a52fe3d286f
Revises: z3d4e5f6g7h8
Create Date: 2026-10-15 13:46:23.163674

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2a52fe3d286f'
down_revision = 'z3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_members') as batch_op:
        batch_op.add_column(sa.Column('rotation_order', sa.Integer(), server_default='0'))


def downgrade():
    with op.batch_alter_table('roster_members') as batch_op:
        batch_op.drop_column('rotation_order')
