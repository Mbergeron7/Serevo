"""Add history tracking to employee assignment tables

- Add end_date to employee_quartiles
- Drop unique constraints that prevent multiple records per employee+entity
- Replace with constraints allowing multiple records differentiated by dates

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade():
    # 1. EmployeePlanningUnit: drop unique constraint on (employee_id, planning_unit_id)
    #    to allow multiple historical records
    with op.batch_alter_table("employee_planning_units") as batch_op:
        batch_op.drop_constraint("uq_emp_pu", type_="unique")

    # 2. EmployeeShiftSequence: drop unique constraint on (employee_id, shift_sequence_id)
    with op.batch_alter_table("employee_shift_sequences") as batch_op:
        batch_op.drop_constraint("uq_emp_ss", type_="unique")

    # 3. EmployeeQuartile: add end_date, drop unique constraint
    with op.batch_alter_table("employee_quartiles") as batch_op:
        batch_op.add_column(sa.Column("end_date", sa.Date(), nullable=True))
        batch_op.drop_constraint("uq_emp_quartile_pu", type_="unique")


def downgrade():
    with op.batch_alter_table("employee_quartiles") as batch_op:
        batch_op.drop_column("end_date")
        batch_op.create_unique_constraint("uq_emp_quartile_pu", ["employee_id", "planning_unit_id"])

    with op.batch_alter_table("employee_shift_sequences") as batch_op:
        batch_op.create_unique_constraint("uq_emp_ss", ["employee_id", "shift_sequence_id"])

    with op.batch_alter_table("employee_planning_units") as batch_op:
        batch_op.create_unique_constraint("uq_emp_pu", ["employee_id", "planning_unit_id"])
