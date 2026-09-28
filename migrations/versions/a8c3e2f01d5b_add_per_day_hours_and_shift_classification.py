"""add per-day operating hours and shift classification fields

Revision ID: a8c3e2f01d5b
Revises: 4726d439d002
Create Date: 2026-09-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c3e2f01d5b'
down_revision = '4726d439d002'
branch_labels = None
depends_on = None


def upgrade():
    # LOB Settings: per-day operating hours
    with op.batch_alter_table('lob_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sat_operating_start', sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column('sat_operating_end', sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column('sun_operating_start', sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column('sun_operating_end', sa.String(length=5), nullable=True))

    # Shift Templates: LOB association, day type, shift category
    with op.batch_alter_table('shift_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('planning_unit_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('day_type', sa.String(length=20), server_default='any', nullable=True))
        batch_op.add_column(sa.Column('shift_category', sa.String(length=20), server_default='any', nullable=True))
        batch_op.create_foreign_key('fk_shift_templates_planning_unit', 'planning_units',
                                    ['planning_unit_id'], ['id'])
        batch_op.create_index('ix_shift_templates_planning_unit_id', ['planning_unit_id'])


def downgrade():
    with op.batch_alter_table('shift_templates', schema=None) as batch_op:
        batch_op.drop_index('ix_shift_templates_planning_unit_id')
        batch_op.drop_constraint('fk_shift_templates_planning_unit', type_='foreignkey')
        batch_op.drop_column('shift_category')
        batch_op.drop_column('day_type')
        batch_op.drop_column('planning_unit_id')

    with op.batch_alter_table('lob_settings', schema=None) as batch_op:
        batch_op.drop_column('sun_operating_end')
        batch_op.drop_column('sun_operating_start')
        batch_op.drop_column('sat_operating_end')
        batch_op.drop_column('sat_operating_start')
