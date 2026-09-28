"""add fill_in_rules table

Revision ID: c3f1d2e45a67
Revises: b7e2a1c34f90
Create Date: 2026-09-28 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3f1d2e45a67'
down_revision = 'b7e2a1c34f90'
branch_labels = None
depends_on = None


def _table_exists(name):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def upgrade():
    if not _table_exists('fill_in_rules'):
        op.create_table('fill_in_rules',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('shift_category', sa.String(length=30), nullable=False),
            sa.Column('employee_id', sa.Integer(), nullable=False),
            sa.Column('priority', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('planning_unit_id', sa.Integer(), nullable=True),
            sa.Column('fallback_template_id', sa.Integer(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['employee_id'], ['employees.id']),
            sa.ForeignKeyConstraint(['planning_unit_id'], ['planning_units.id']),
            sa.ForeignKeyConstraint(['fallback_template_id'], ['shift_templates.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('shift_category', 'employee_id', name='uq_fillin_cat_employee'),
        )
        op.create_index('ix_fill_in_rules_employee_id', 'fill_in_rules', ['employee_id'])
        op.create_index('ix_fill_in_rules_category', 'fill_in_rules', ['shift_category'])


def downgrade():
    op.drop_index('ix_fill_in_rules_category', table_name='fill_in_rules')
    op.drop_index('ix_fill_in_rules_employee_id', table_name='fill_in_rules')
    op.drop_table('fill_in_rules')
