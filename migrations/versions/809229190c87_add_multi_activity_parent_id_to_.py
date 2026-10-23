"""Add multi-activity parent_id to planning_units

Revision ID: 809229190c87
Revises: zb2c3d4e5f6g
Create Date: 2026-10-23 09:44:11.831465

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '809229190c87'
down_revision = 'zb2c3d4e5f6g'
branch_labels = None
depends_on = None


def upgrade():
    # Only add the two new planning_units columns for multi-activity support
    with op.batch_alter_table('planning_units', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_multi_activity', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_pu_parent_id', 'planning_units', ['parent_id'], ['id'])


def downgrade():
    with op.batch_alter_table('planning_units', schema=None) as batch_op:
        batch_op.drop_constraint('fk_pu_parent_id', type_='foreignkey')
        batch_op.drop_column('parent_id')
        batch_op.drop_column('is_multi_activity')
