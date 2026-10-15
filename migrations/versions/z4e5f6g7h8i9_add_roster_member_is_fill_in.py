"""Add is_fill_in column to roster_members

Revision ID: z4e5f6g7h8i9
Revises: a1b2c3d4e5f6
Create Date: 2026-10-15 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'z4e5f6g7h8i9'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Check if column already exists (db.create_all may have created it)
    bind = op.get_bind()
    from sqlalchemy import inspect
    insp = inspect(bind)
    cols = [c['name'] for c in insp.get_columns('roster_members')]
    if 'is_fill_in' not in cols:
        op.add_column('roster_members',
                       sa.Column('is_fill_in', sa.Boolean(), server_default='0', nullable=True))


def downgrade():
    op.drop_column('roster_members', 'is_fill_in')
