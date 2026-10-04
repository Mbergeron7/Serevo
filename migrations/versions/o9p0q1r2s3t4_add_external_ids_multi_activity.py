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


def _column_exists(table, column):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c)"
    ), {"t": table, "c": column})
    return result.scalar()


def _constraint_exists(name):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = :n)"
    ), {"n": name})
    return result.scalar()


def upgrade():
    # SegmentCode: activity_category, external_ids, parent_id, is_multi_activity
    with op.batch_alter_table('segment_codes', schema=None) as batch_op:
        for col_name, col_type, default in [
            ('activity_category', sa.String(20), 'status'),
            ('external_ids', sa.Text(), None),
            ('parent_id', sa.Integer(), None),
            ('is_multi_activity', sa.Boolean(), '0'),
        ]:
            if not _column_exists('segment_codes', col_name):
                batch_op.add_column(sa.Column(col_name, col_type, server_default=default, nullable=True))
        if not _constraint_exists('fk_segment_codes_parent'):
            batch_op.create_foreign_key('fk_segment_codes_parent', 'segment_codes', ['parent_id'], ['id'])

    # Activity: activity_category, external_ids, parent_id, is_multi_activity
    with op.batch_alter_table('activities', schema=None) as batch_op:
        for col_name, col_type, default in [
            ('activity_category', sa.String(20), 'status'),
            ('external_ids', sa.Text(), None),
            ('parent_id', sa.Integer(), None),
            ('is_multi_activity', sa.Boolean(), '0'),
        ]:
            if not _column_exists('activities', col_name):
                batch_op.add_column(sa.Column(col_name, col_type, server_default=default, nullable=True))
        if not _constraint_exists('fk_activities_parent'):
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
