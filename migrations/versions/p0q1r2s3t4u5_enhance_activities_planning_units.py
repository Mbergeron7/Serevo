"""Enhance activities with PeopleWare fields and planning unit config

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'p0q1r2s3t4u5'
down_revision = 'o9p0q1r2s3t4'
branch_labels = None
depends_on = None


def upgrade():
    # ── SegmentCode: new PeopleWare-level fields ─────────────
    with op.batch_alter_table('segment_codes', schema=None) as batch_op:
        # Activity type (presence/break/absence/meeting/vacation)
        batch_op.add_column(sa.Column('activity_type', sa.String(20), server_default='presence', nullable=True))
        # Naming
        batch_op.add_column(sa.Column('official_name', sa.String(120), nullable=True))
        batch_op.add_column(sa.Column('abbreviation', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('shortcut', sa.String(10), nullable=True))
        # Scheduling behavior flags
        batch_op.add_column(sa.Column('is_replaceable', sa.Boolean(), server_default='1', nullable=True))
        batch_op.add_column(sa.Column('is_plannable', sa.Boolean(), server_default='1', nullable=True))
        batch_op.add_column(sa.Column('importance', sa.Integer(), server_default='50', nullable=True))
        batch_op.add_column(sa.Column('priority', sa.Integer(), server_default='50', nullable=True))
        batch_op.add_column(sa.Column('comply_rest_period', sa.Boolean(), server_default='1', nullable=True))
        batch_op.add_column(sa.Column('allow_overstaffing_zero', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('is_requestable', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('is_exchangeable', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('allow_full_day', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('special_handling', sa.Boolean(), server_default='0', nullable=True))
        batch_op.add_column(sa.Column('can_be_day_status', sa.Boolean(), server_default='0', nullable=True))

    # ── ActivitySkill table ──────────────────────────────────
    op.create_table('activity_skills',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('segment_code_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('weighting', sa.Integer(), server_default='100', nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['segment_code_id'], ['segment_codes.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # ── ExternalStatusMapping table ──────────────────────────
    op.create_table('external_status_mappings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('segment_code_id', sa.Integer(), nullable=False),
        sa.Column('external_status', sa.String(100), nullable=False),
        sa.Column('description', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['segment_code_id'], ['segment_codes.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # ── PlanningUnit: new fields ─────────────────────────────
    with op.batch_alter_table('planning_units', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('timezone', sa.String(60), server_default='America/New_York', nullable=True))

    # ── PlanningUnitBusinessHours table ──────────────────────
    op.create_table('planning_unit_business_hours',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('planning_unit_id', sa.Integer(), nullable=False),
        sa.Column('day_type', sa.String(20), nullable=False),
        sa.Column('open_time', sa.String(5), nullable=False),
        sa.Column('close_time', sa.String(5), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['planning_unit_id'], ['planning_units.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # ── PlanningUnitActivity table ───────────────────────────
    op.create_table('planning_unit_activities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('planning_unit_id', sa.Integer(), nullable=False),
        sa.Column('segment_code_id', sa.Integer(), nullable=False),
        sa.Column('window_start', sa.String(5), nullable=True),
        sa.Column('window_end', sa.String(5), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['planning_unit_id'], ['planning_units.id']),
        sa.ForeignKeyConstraint(['segment_code_id'], ['segment_codes.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # ── PlanningUnitParameter table ──────────────────────────
    op.create_table('planning_unit_parameters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('planning_unit_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('lower_limit', sa.Float(), nullable=True),
        sa.Column('upper_limit', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['planning_unit_id'], ['planning_units.id']),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('planning_unit_parameters')
    op.drop_table('planning_unit_activities')
    op.drop_table('planning_unit_business_hours')

    with op.batch_alter_table('planning_units', schema=None) as batch_op:
        batch_op.drop_column('timezone')
        batch_op.drop_column('description')

    op.drop_table('external_status_mappings')
    op.drop_table('activity_skills')

    with op.batch_alter_table('segment_codes', schema=None) as batch_op:
        batch_op.drop_column('can_be_day_status')
        batch_op.drop_column('special_handling')
        batch_op.drop_column('allow_full_day')
        batch_op.drop_column('is_exchangeable')
        batch_op.drop_column('is_requestable')
        batch_op.drop_column('allow_overstaffing_zero')
        batch_op.drop_column('comply_rest_period')
        batch_op.drop_column('priority')
        batch_op.drop_column('importance')
        batch_op.drop_column('is_plannable')
        batch_op.drop_column('is_replaceable')
        batch_op.drop_column('shortcut')
        batch_op.drop_column('abbreviation')
        batch_op.drop_column('official_name')
        batch_op.drop_column('activity_type')
