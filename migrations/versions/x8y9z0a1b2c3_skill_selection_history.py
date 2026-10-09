"""Add history tracking to skill_mappings and selection_members

- Add valid_from/valid_to to skill_mappings
- Add valid_from/valid_to to selection_members
- Drop unique constraints that prevent multiple records per employee+entity

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("skill_mappings") as batch_op:
        batch_op.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch_op.drop_constraint("uq_skill_employee", type_="unique")

    with op.batch_alter_table("selection_members") as batch_op:
        batch_op.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch_op.drop_constraint("uq_sel_emp", type_="unique")


def downgrade():
    with op.batch_alter_table("selection_members") as batch_op:
        batch_op.drop_column("valid_to")
        batch_op.drop_column("valid_from")
        batch_op.create_unique_constraint("uq_sel_emp", ["selection_id", "employee_id"])

    with op.batch_alter_table("skill_mappings") as batch_op:
        batch_op.drop_column("valid_to")
        batch_op.drop_column("valid_from")
        batch_op.create_unique_constraint("uq_skill_employee", ["skill_group_id", "employee_id"])
