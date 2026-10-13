"""Add quartile_schedule_rules table

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-10-13 15:55:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'z0a1b2c3d4e5'
down_revision = 'y9z0a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade():
    # Guard: skip if table already exists (created manually or by auto-sync)
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'quartile_schedule_rules'"
    ))
    if result.fetchone():
        return

    op.create_table(
        'quartile_schedule_rules',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('planning_unit_id', sa.Integer,
                  sa.ForeignKey('planning_units.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('quartile', sa.Integer, nullable=False),
        sa.Column('day_model_id', sa.Integer,
                  sa.ForeignKey('day_models.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('priority', sa.Integer, default=0),
        sa.Column('created_at', sa.DateTime),
        sa.UniqueConstraint('planning_unit_id', 'quartile', 'day_model_id',
                            name='uq_quartile_rule'),
    )


def downgrade():
    op.drop_table('quartile_schedule_rules')
