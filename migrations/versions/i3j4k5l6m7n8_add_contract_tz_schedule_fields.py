"""add contract, timezone, schedule_excluded fields to employees

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-09-29 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i3j4k5l6m7n8'
down_revision = 'h2i3j4k5l6m7'
branch_labels = None
depends_on = None


def _has_column(table, column):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c['name'] for c in insp.get_columns(table)]
    return column in columns


def upgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        if not _has_column('employees', 'contract_type'):
            batch_op.add_column(sa.Column('contract_type', sa.String(20),
                                          server_default='Full-Time', nullable=True))
        if not _has_column('employees', 'weekly_hours'):
            batch_op.add_column(sa.Column('weekly_hours', sa.Float(),
                                          server_default='40.0', nullable=True))
        if not _has_column('employees', 'days_per_week'):
            batch_op.add_column(sa.Column('days_per_week', sa.Integer(),
                                          server_default='5', nullable=True))
        if not _has_column('employees', 'hours_per_day'):
            batch_op.add_column(sa.Column('hours_per_day', sa.Float(),
                                          server_default='8.0', nullable=True))
        if not _has_column('employees', 'timezone'):
            batch_op.add_column(sa.Column('timezone', sa.String(60),
                                          server_default='America/New_York', nullable=True))
        if not _has_column('employees', 'schedule_excluded'):
            batch_op.add_column(sa.Column('schedule_excluded', sa.Boolean(),
                                          server_default='false', nullable=True))


def downgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        for col in ('contract_type', 'weekly_hours', 'days_per_week',
                     'hours_per_day', 'timezone', 'schedule_excluded'):
            if _has_column('employees', col):
                batch_op.drop_column(col)
