"""Add break/lunch window columns to contracts

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-10-13 15:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'y9z0a1b2c3d4'
down_revision = 'x8y9z0a1b2c3'
branch_labels = None
depends_on = None


def upgrade():
    for col in ('break_earliest', 'break_latest', 'lunch_earliest', 'lunch_latest'):
        op.add_column('contracts', sa.Column(col, sa.String(5), nullable=True))


def downgrade():
    for col in ('break_earliest', 'break_latest', 'lunch_earliest', 'lunch_latest'):
        op.drop_column('contracts', col)
