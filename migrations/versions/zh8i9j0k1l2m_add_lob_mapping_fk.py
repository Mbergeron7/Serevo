"""Add planning_unit_id FK to lob_mappings

Revision ID: zh8i9j0k1l2m
Revises: zg7h8i9j0k1l
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "zh8i9j0k1l2m"
down_revision = "zg7h8i9j0k1l"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("lob_mappings") as batch_op:
        batch_op.add_column(sa.Column("planning_unit_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_lob_mappings_planning_unit_id", ["planning_unit_id"])
        batch_op.create_foreign_key(
            "fk_lob_mappings_planning_unit_id",
            "planning_units",
            ["planning_unit_id"],
            ["id"],
        )

    # Backfill: resolve planning_unit_name → planning_unit_id
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE lob_mappings
        SET planning_unit_id = (
            SELECT id FROM planning_units
            WHERE planning_units.name = lob_mappings.planning_unit_name
            LIMIT 1
        )
        WHERE planning_unit_id IS NULL
    """))


def downgrade():
    with op.batch_alter_table("lob_mappings") as batch_op:
        batch_op.drop_constraint("fk_lob_mappings_planning_unit_id", type_="foreignkey")
        batch_op.drop_index("ix_lob_mappings_planning_unit_id")
        batch_op.drop_column("planning_unit_id")
