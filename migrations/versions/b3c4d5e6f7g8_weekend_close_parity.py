"""Add weekend_close_week_parity to roster_groups

Revision ID: b3c4d5e6f7g8
Revises: a2b3c4d5e6f7
Create Date: 2026-10-15 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3c4d5e6f7g8'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    from sqlalchemy import inspect
    insp = inspect(bind)

    cols = [c['name'] for c in insp.get_columns('roster_groups')]
    if 'weekend_close_week_parity' not in cols:
        op.add_column('roster_groups',
                       sa.Column('weekend_close_week_parity', sa.String(4), nullable=True))


def downgrade():
    op.drop_column('roster_groups', 'weekend_close_week_parity')
