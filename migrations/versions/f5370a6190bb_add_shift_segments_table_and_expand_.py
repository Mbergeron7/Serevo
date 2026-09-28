"""add shift_segments table and expand schedules

Revision ID: f5370a6190bb
Revises: 940a3a9c7df4
Create Date: 2026-09-26 10:25:17.080498

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5370a6190bb'
down_revision = '940a3a9c7df4'
branch_labels = None
depends_on = None


def _table_exists(name):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _has_column(table, column):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c['name'] for c in insp.get_columns(table)]
    return column in columns


def upgrade():
    if not _table_exists('shift_segments'):
        op.create_table('shift_segments',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('schedule_id', sa.Integer(), nullable=False),
            sa.Column('activity_type', sa.String(length=20), nullable=False),
            sa.Column('start_time', sa.Time(), nullable=False),
            sa.Column('end_time', sa.Time(), nullable=False),
            sa.Column('duration_mins', sa.Integer(), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=True),
            sa.Column('notes', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['schedule_id'], ['schedules.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_seg_schedule', 'shift_segments', ['schedule_id', 'sort_order'])

    with op.batch_alter_table('schedules', schema=None) as batch_op:
        if not _has_column('schedules', 'shift_type'):
            batch_op.add_column(sa.Column('shift_type', sa.String(length=10), nullable=True))
        if not _has_column('schedules', 'hours'):
            batch_op.add_column(sa.Column('hours', sa.Float(), nullable=True))
        if not _has_column('schedules', 'updated_at'):
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('hours')
        batch_op.drop_column('shift_type')

    op.drop_index('ix_seg_schedule', table_name='shift_segments')
    op.drop_table('shift_segments')
