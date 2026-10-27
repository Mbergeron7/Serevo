"""Wire planning calendars: add volume_factor to day_types, calendar_id to planning_units.

Revision ID: zo5p6q7r8s9t
Revises: zn4o5p6q7r8s
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = 'zo5p6q7r8s9t'
down_revision = 'zn4o5p6q7r8s'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('day_types') as batch_op:
        batch_op.add_column(sa.Column('volume_factor', sa.Float(), default=0.0))

    with op.batch_alter_table('planning_units') as batch_op:
        batch_op.add_column(sa.Column('calendar_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_pu_calendar', 'planning_calendars', ['calendar_id'], ['id'])


def downgrade():
    with op.batch_alter_table('planning_units') as batch_op:
        batch_op.drop_constraint('fk_pu_calendar', type_='foreignkey')
        batch_op.drop_column('calendar_id')

    with op.batch_alter_table('day_types') as batch_op:
        batch_op.drop_column('volume_factor')
