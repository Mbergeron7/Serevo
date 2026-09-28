"""add employee_availability table

Revision ID: e5b3c4d67f89
Revises: d4a2b3c56e78
Create Date: 2026-09-28 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b3c4d67f89'
down_revision = 'd4a2b3c56e78'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if 'employee_availability' not in insp.get_table_names():
        op.create_table(
            'employee_availability',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('employee_id', sa.Integer(),
                       sa.ForeignKey('employees.id', ondelete='CASCADE'),
                       nullable=False, index=True),
            sa.Column('day_of_week', sa.Integer(), nullable=False),
            sa.Column('is_available', sa.Boolean(), default=True),
            sa.Column('earliest_start', sa.Time(), nullable=True),
            sa.Column('latest_start', sa.Time(), nullable=True),
            sa.Column('latest_end', sa.Time(), nullable=True),
            sa.Column('notes', sa.String(length=255), default=''),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('employee_id', 'day_of_week', name='uq_avail_emp_day'),
        )


def downgrade():
    op.drop_table('employee_availability')
