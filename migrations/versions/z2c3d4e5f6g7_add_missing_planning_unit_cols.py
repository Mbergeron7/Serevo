"""Add missing planning_unit_id columns to day_models and week_time_patterns

Revision ID: z2c3d4e5f6g7
Revises: z1b2c3d4e5f6
Create Date: 2026-10-13 21:20:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'z2c3d4e5f6g7'
down_revision = 'z1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _col_exists(conn, table, column):
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade():
    conn = op.get_bind()

    if not _col_exists(conn, 'day_models', 'planning_unit_id'):
        op.add_column('day_models',
                       sa.Column('planning_unit_id', sa.Integer,
                                 sa.ForeignKey('planning_units.id'),
                                 nullable=True))

    if not _col_exists(conn, 'week_time_patterns', 'planning_unit_id'):
        op.add_column('week_time_patterns',
                       sa.Column('planning_unit_id', sa.Integer,
                                 sa.ForeignKey('planning_units.id'),
                                 nullable=True))


def downgrade():
    op.drop_column('week_time_patterns', 'planning_unit_id')
    op.drop_column('day_models', 'planning_unit_id')
