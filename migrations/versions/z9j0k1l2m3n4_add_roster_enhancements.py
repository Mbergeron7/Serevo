"""Add roster enhancements: max_consecutive_days, preferred_days_off,
hours guardrails, pairing, role rotation tracking

Revision ID: z9j0k1l2m3n4
Revises: z8i9j0k1l2m3
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'z9j0k1l2m3n4'
down_revision = 'z8i9j0k1l2m3'
branch_labels = None
depends_on = None


def upgrade():
    # -- RosterMember new columns --
    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_consecutive_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('preferred_days_off', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('min_hours_per_week', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('max_hours_per_week', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('pair_with_member_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('avoid_member_id', sa.Integer(), nullable=True))

    # -- RosterRotationState new columns --
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role_counts_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('consecutive_days', sa.Integer(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('last_work_date', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('roster_rotation_states', schema=None) as batch_op:
        batch_op.drop_column('last_work_date')
        batch_op.drop_column('consecutive_days')
        batch_op.drop_column('role_counts_json')

    with op.batch_alter_table('roster_members', schema=None) as batch_op:
        batch_op.drop_column('avoid_member_id')
        batch_op.drop_column('pair_with_member_id')
        batch_op.drop_column('max_hours_per_week')
        batch_op.drop_column('min_hours_per_week')
        batch_op.drop_column('preferred_days_off')
        batch_op.drop_column('max_consecutive_days')
