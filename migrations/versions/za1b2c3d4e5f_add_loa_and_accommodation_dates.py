"""Add leave_of_absences table and date fields to accommodations

Revision ID: za1b2c3d4e5f
Revises: z12c3d4e5f6g7
Create Date: 2026-10-22
"""
from alembic import op
import sqlalchemy as sa

revision = "za1b2c3d4e5f"
down_revision = "z12c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade():
    # Add start_date and end_date to accommodations (skip if already exist)
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    accom_cols = [c["name"] for c in inspector.get_columns("accommodations")]
    with op.batch_alter_table("accommodations") as batch_op:
        if "start_date" not in accom_cols:
            batch_op.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        if "end_date" not in accom_cols:
            batch_op.add_column(sa.Column("end_date", sa.Date(), nullable=True))

    # Create leave_of_absences table only if it doesn't already exist
    if not inspector.has_table("leave_of_absences"):
        op.create_table(
            "leave_of_absences",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False, index=True),
            sa.Column("leave_type", sa.String(50), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("expected_return_date", sa.Date(), nullable=True),
            sa.Column("actual_return_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(20), server_default="Active"),
            sa.Column("notes", sa.Text(), server_default=""),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        )


def downgrade():
    op.drop_table("leave_of_absences")
    with op.batch_alter_table("accommodations") as batch_op:
        batch_op.drop_column("end_date")
        batch_op.drop_column("start_date")
