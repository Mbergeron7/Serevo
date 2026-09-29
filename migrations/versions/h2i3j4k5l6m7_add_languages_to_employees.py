"""add languages to employees

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-09-29 18:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h2i3j4k5l6m7'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on = None


def _has_column(table, column):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c['name'] for c in insp.get_columns(table)]
    return column in columns


def upgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        if not _has_column('employees', 'languages'):
            batch_op.add_column(sa.Column('languages', sa.String(100),
                                          server_default='English', nullable=True))


def downgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        if _has_column('employees', 'languages'):
            batch_op.drop_column('languages')
