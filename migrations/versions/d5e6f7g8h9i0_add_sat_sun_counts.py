"""Add saturday_count and sunday_count to roster_rotation_states

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-10-16 16:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd5e6f7g8h9i0'
down_revision = 'c4d5e6f7g8h9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.add_column(sa.Column('saturday_count', sa.Integer(), nullable=True, server_default='0'))
        batch_op.add_column(sa.Column('sunday_count', sa.Integer(), nullable=True, server_default='0'))


def downgrade():
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.drop_column('sunday_count')
        batch_op.drop_column('saturday_count')
