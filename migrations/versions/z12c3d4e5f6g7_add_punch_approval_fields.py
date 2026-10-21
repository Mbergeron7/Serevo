"""Add admin punch approval fields to time_clock.

Revision ID: z12c3d4e5f6g7
Revises: z11b2c3d4e5f6
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = 'z12c3d4e5f6g7'
down_revision = 'z11b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("time_clock") as batch_op:
        batch_op.add_column(sa.Column("original_clock_in", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("original_clock_out", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("approval_status", sa.String(20), server_default="pending", nullable=True))
        batch_op.add_column(sa.Column("approved_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("admin_note", sa.String(500), nullable=True))


def downgrade():
    with op.batch_alter_table("time_clock") as batch_op:
        batch_op.drop_column("admin_note")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
        batch_op.drop_column("approval_status")
        batch_op.drop_column("original_clock_out")
        batch_op.drop_column("original_clock_in")
