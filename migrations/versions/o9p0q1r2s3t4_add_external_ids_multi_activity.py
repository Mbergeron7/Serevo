"""Add external_ids and multi-activity fields to segment_codes and activities

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'o9p0q1r2s3t4'
down_revision = 'n8o9p0q1r2s3'
branch_labels = None
depends_on = None


def upgrade():
    # SegmentCode: activity_category, external_ids, parent_id, is_multi_activity
    with op.batch_alter_table('segment_codes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('activity_category', sa.String(20), server_default='status', nullable=True))
        batch_op.add_column(sa.Column('external_ids', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('is_multi_activity', sa.Boolean(), server_default='0', nullable=True))
        batch_op.create_foreign_key('fk_segment_codes_parent', 'segment_codes', ['parent_id'], ['id'])

    # Activity: activity_category, external_ids, parent_id, is_multi_activity
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('activity_category', sa.String(20), server_default='status', nullable=True))
        batch_op.add_column(sa.Column('external_ids', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('parent_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('is_multi_activity', sa.Boolean(), server_default='0', nullable=True))
        batch_op.create_foreign_key('fk_activities_parent', 'activities', ['parent_id'], ['id'])


def downgrade():
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.drop_constraint('fk_activities_parent', type_='foreignkey')
        batch_op.drop_column('is_multi_activity')
        batch_op.drop_column('parent_id')
        batch_op.drop_column('external_ids')
        batch_op.drop_column('activity_category')

    with op.batch_alter_table('segment_codes', schema=None) as batch_op:
        batch_op.drop_constraint('fk_segment_codes_parent', type_='foreignkey')
        batch_op.drop_column('is_multi_activity')
        batch_op.drop_column('parent_id')
        batch_op.drop_column('external_ids')
        batch_op.drop_column('activity_category')
