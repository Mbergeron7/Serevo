"""add missing schedule columns (shift_type, hours, updated_at)

Revision ID: d4a2b3c56e78
Revises: c3f1d2e45a67
Create Date: 2026-09-28 17:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a2b3c56e78'
down_revision = 'c3f1d2e45a67'
branch_labels = None
depends_on = None


def _has_column(table, column):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c['name'] for c in insp.get_columns(table)]
    return column in columns


def upgrade():
    if not _has_column('schedules', 'shift_type'):
        op.add_column('schedules', sa.Column('shift_type', sa.String(length=10), nullable=True))
    if not _has_column('schedules', 'hours'):
        op.add_column('schedules', sa.Column('hours', sa.Float(), nullable=True))
    if not _has_column('schedules', 'updated_at'):
        op.add_column('schedules', sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('schedules', 'updated_at')
    op.drop_column('schedules', 'hours')
    op.drop_column('schedules', 'shift_type')
