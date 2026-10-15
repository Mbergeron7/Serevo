"""Roster slot rework: drop unique constraint, add label/sort_order, add sunday_non_closer

Revision ID: a2b3c4d5e6f7
Revises: z4e5f6g7h8i9
Create Date: 2026-10-15 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'z4e5f6g7h8i9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    from sqlalchemy import inspect
    insp = inspect(bind)

    # --- roster_slots changes ---
    cols = [c['name'] for c in insp.get_columns('roster_slots')]

    # Drop UniqueConstraint if it exists
    try:
        op.drop_constraint('uq_roster_slot', 'roster_slots', type_='unique')
    except Exception:
        pass  # constraint may not exist (db.create_all path)

    if 'label' not in cols:
        op.add_column('roster_slots',
                       sa.Column('label', sa.String(60), nullable=True))
    if 'sort_order' not in cols:
        op.add_column('roster_slots',
                       sa.Column('sort_order', sa.Integer(), server_default='0', nullable=True))

    # Widen role column from 20 to 40 chars
    # SQLite doesn't enforce length, so only run ALTER for postgres
    dialect = bind.dialect.name
    if dialect != 'sqlite':
        op.alter_column('roster_slots', 'role',
                        type_=sa.String(40), existing_type=sa.String(20))

    # --- roster_groups changes ---
    grp_cols = [c['name'] for c in insp.get_columns('roster_groups')]
    if 'sunday_non_closer' not in grp_cols:
        op.add_column('roster_groups',
                       sa.Column('sunday_non_closer', sa.Boolean(), server_default='0', nullable=True))


def downgrade():
    op.drop_column('roster_groups', 'sunday_non_closer')
    op.drop_column('roster_slots', 'sort_order')
    op.drop_column('roster_slots', 'label')
    op.create_unique_constraint('uq_roster_slot', 'roster_slots',
                                ['roster_group_id', 'day_type', 'role'])
