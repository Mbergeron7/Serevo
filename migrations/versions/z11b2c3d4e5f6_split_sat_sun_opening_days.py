"""Split exclude_from_weekends into sat/sun, add excluded_opening_days,
allow_split_shift, split_shift_min_gap

Revision ID: z11b2c3d4e5f6
Revises: z10a1b2c3d4e5
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'z11b2c3d4e5f6'
down_revision = 'z10a1b2c3d4e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exclude_from_saturday', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('exclude_from_sunday', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('excluded_opening_days', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('allow_split_shift', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('split_shift_min_gap', sa.Float(), nullable=True))

    # Migrate existing exclude_from_weekends data into new columns
    op.execute("""
        UPDATE roster_members
        SET exclude_from_saturday = exclude_from_weekends,
            exclude_from_sunday = exclude_from_weekends
        WHERE exclude_from_weekends = true
    """)


def downgrade():
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.drop_column('split_shift_min_gap')
        batch_op.drop_column('allow_split_shift')
        batch_op.drop_column('excluded_opening_days')
        batch_op.drop_column('exclude_from_sunday')
        batch_op.drop_column('exclude_from_saturday')
