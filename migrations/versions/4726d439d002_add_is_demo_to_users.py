"""add is_demo to users

Revision ID: 4726d439d002
Revises: f5370a6190bb
Create Date: 2026-09-26 17:42:20.997862

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4726d439d002'
down_revision = 'f5370a6190bb'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_demo', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_demo')
