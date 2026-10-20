"""Add member role flags: dedicated_closer, dedicated_opener, exclude_from_weekends, exclude_from_opening, exclude_from_closing

Revision ID: z7h8i9j0k1l2
Revises: z6g7h8i9j0k1
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'z7h8i9j0k1l2'
down_revision = 'z6g7h8i9j0k1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_members') as batch_op:
        batch_op.add_column(sa.Column('dedicated_closer', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('dedicated_opener', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('exclude_from_weekends', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('exclude_from_opening', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('exclude_from_closing', sa.Boolean(), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('roster_members') as batch_op:
        batch_op.drop_column('exclude_from_closing')
        batch_op.drop_column('exclude_from_opening')
        batch_op.drop_column('exclude_from_weekends')
        batch_op.drop_column('dedicated_opener')
        batch_op.drop_column('dedicated_closer')
