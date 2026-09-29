"""add interval_actuals and agent_status_events tables

Revision ID: a7d8e9f01b23
Revises: f6c4d5e78a90
Create Date: 2026-09-29 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "a7d8e9f01b23"
down_revision = "f6c4d5e78a90"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "interval_actuals" not in tables:
        op.create_table(
            "interval_actuals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("planning_unit_id", sa.Integer(), sa.ForeignKey("planning_units.id"), nullable=False, index=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False, index=True),
            sa.Column("offered", sa.Integer(), server_default="0"),
            sa.Column("answered", sa.Integer(), server_default="0"),
            sa.Column("answered_within", sa.Integer(), server_default="0"),
            sa.Column("abandoned", sa.Integer(), server_default="0"),
            sa.Column("rolled", sa.Integer(), server_default="0"),
            sa.Column("asa_secs", sa.Float(), nullable=True),
            sa.Column("aht_secs", sa.Float(), nullable=True),
            sa.Column("max_queued", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(30), server_default="upload"),
            sa.Column("uploaded_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("planning_unit_id", "timestamp", name="uq_actual_unit_ts"),
        )

    if "agent_status_events" not in tables:
        op.create_table(
            "agent_status_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False, index=True),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("start_ts", sa.DateTime(), nullable=False, index=True),
            sa.Column("end_ts", sa.DateTime(), nullable=True),
            sa.Column("source", sa.String(30), server_default="upload"),
            sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_agent_status_emp_start", "agent_status_events", ["employee_id", "start_ts"])


def downgrade():
    op.drop_table("agent_status_events")
    op.drop_table("interval_actuals")
