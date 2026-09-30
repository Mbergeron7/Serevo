"""Add data_sources table and data_source_id to interval_actuals

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa

revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        # Google Sheets
        sa.Column("sheet_key", sa.String(200), server_default=""),
        sa.Column("tab_name", sa.String(100), server_default=""),
        sa.Column("service_account_json", sa.Text(), server_default=""),
        # API
        sa.Column("api_connection_id", sa.Integer(),
                  sa.ForeignKey("api_connections.id"), nullable=True),
        sa.Column("api_endpoint", sa.String(500), server_default=""),
        # Mapping & filter
        sa.Column("column_mapping", sa.JSON(), server_default="{}"),
        sa.Column("planning_unit_ids", sa.JSON(), server_default="[]"),
        # Sync status
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("last_sync", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(50), server_default="never_synced"),
        sa.Column("last_error", sa.Text(), server_default=""),
        sa.Column("rows_synced", sa.Integer(), server_default="0"),
        sa.Column("sync_interval_hours", sa.Integer(), server_default="24"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # Add data_source_id FK to interval_actuals
    with op.batch_alter_table("interval_actuals") as batch_op:
        batch_op.add_column(
            sa.Column("data_source_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_interval_actuals_data_source_id",
                              ["data_source_id"])
        batch_op.create_foreign_key(
            "fk_interval_actuals_data_source",
            "data_sources", ["data_source_id"], ["id"])


def downgrade():
    with op.batch_alter_table("interval_actuals") as batch_op:
        batch_op.drop_constraint("fk_interval_actuals_data_source",
                                 type_="foreignkey")
        batch_op.drop_index("ix_interval_actuals_data_source_id")
        batch_op.drop_column("data_source_id")
    op.drop_table("data_sources")
