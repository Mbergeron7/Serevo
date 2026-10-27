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


def _table_exists(table):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"
    ), {"t": table})
    return result.scalar()


def upgrade():
    # SegmentCode: activity_category, external_ids, parent_id, is_multi_activity
    for col_name, col_type, default in [
        ('activity_category', sa.String(20), 'status'),
        ('external_ids', sa.Text(), None),
        ('parent_id', sa.Integer(), None),
        ('is_multi_activity', sa.Boolean(), '0'),
    ]:
        if not _column_exists('segment_codes', col_name):
            op.add_column('segment_codes', sa.Column(col_name, col_type, server_default=default, nullable=True))
    if not _constraint_exists('fk_segment_codes_parent'):
        op.create_foreign_key('fk_segment_codes_parent', 'segment_codes', 'segment_codes', ['parent_id'], ['id'])

    # Activity: activity_category, external_ids, parent_id, is_multi_activity
    # Only if the activities table still exists (it gets dropped by a later migration)
    if _table_exists('activities'):
        for col_name, col_type, default in [
            ('activity_category', sa.String(20), 'status'),
            ('external_ids', sa.Text(), None),
            ('parent_id', sa.Integer(), None),
            ('is_multi_activity', sa.Boolean(), '0'),
        ]:
            if not _column_exists('activities', col_name):
                op.add_column('activities', sa.Column(col_name, col_type, server_default=default, nullable=True))
        if not _constraint_exists('fk_activities_parent'):
            op.create_foreign_key('fk_activities_parent', 'activities', 'activities', ['parent_id'], ['id'])


def downgrade():
    if _table_exists('activities'):
        op.drop_constraint('fk_activities_parent', 'activities', type_='foreignkey')
        op.drop_column('activities', 'is_multi_activity')
        op.drop_column('activities', 'parent_id')
        op.drop_column('activities', 'external_ids')
        op.drop_column('activities', 'activity_category')

    op.drop_constraint('fk_segment_codes_parent', 'segment_codes', type_='foreignkey')
    op.drop_column('segment_codes', 'is_multi_activity')
    op.drop_column('segment_codes', 'parent_id')
    op.drop_column('segment_codes', 'external_ids')
    op.drop_column('segment_codes', 'activity_category')
