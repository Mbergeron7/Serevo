"""Add CASCADE/SET NULL to shift_swap_requests schedule FKs

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-10-08
"""
from alembic import op

revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old FKs and recreate with cascade rules
    op.drop_constraint(
        "shift_swap_requests_requester_schedule_id_fkey",
        "shift_swap_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shift_swap_requests_requester_schedule_id_fkey",
        "shift_swap_requests",
        "schedules",
        ["requester_schedule_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "shift_swap_requests_target_schedule_id_fkey",
        "shift_swap_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shift_swap_requests_target_schedule_id_fkey",
        "shift_swap_requests",
        "schedules",
        ["target_schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "shift_swap_requests_requester_schedule_id_fkey",
        "shift_swap_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shift_swap_requests_requester_schedule_id_fkey",
        "shift_swap_requests",
        "schedules",
        ["requester_schedule_id"],
        ["id"],
    )

    op.drop_constraint(
        "shift_swap_requests_target_schedule_id_fkey",
        "shift_swap_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "shift_swap_requests_target_schedule_id_fkey",
        "shift_swap_requests",
        "schedules",
        ["target_schedule_id"],
        ["id"],
    )
