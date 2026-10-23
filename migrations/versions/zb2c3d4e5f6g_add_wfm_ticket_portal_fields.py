"""Add WFM ticket portal-style fields

Revision ID: zb2c3d4e5f6g
Revises: za1b2c3d4e5f
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'zb2c3d4e5f6g'
down_revision = 'za1b2c3d4e5f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('wfm_ticket', schema=None) as batch_op:
        batch_op.add_column(sa.Column('affected_date_end', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('lob', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('shift_time', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('overtime_hours', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('swap_with', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('wfm_ticket', schema=None) as batch_op:
        batch_op.drop_column('swap_with')
        batch_op.drop_column('overtime_hours')
        batch_op.drop_column('shift_time')
        batch_op.drop_column('lob')
        batch_op.drop_column('affected_date_end')
