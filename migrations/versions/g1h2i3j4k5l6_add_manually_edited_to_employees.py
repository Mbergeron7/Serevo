"""add manually_edited to employees

Revision ID: g1h2i3j4k5l6
Revises: 4726d439d002
Create Date: 2026-09-29 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'g1h2i3j4k5l6'
down_revision = '4726d439d002'
branch_labels = None
depends_on = None


def _has_column(table, column):
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = [c['name'] for c in insp.get_columns(table)]
    return column in columns


def upgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        if not _has_column('employees', 'manually_edited'):
            batch_op.add_column(sa.Column('manually_edited', sa.Boolean(),
                                          server_default='false', nullable=True))


def downgrade():
    with op.batch_alter_table('employees', schema=None) as batch_op:
        if _has_column('employees', 'manually_edited'):
            batch_op.drop_column('manually_edited')
