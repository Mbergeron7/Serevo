"""Enhance contracts with full PeopleWare-level scheduling parameters

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-10-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q1r2s3t4u5v6'
down_revision = 'p0q1r2s3t4u5'
branch_labels = None
depends_on = None


def _column_exists(table, column):
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c)"
    ), {"t": table, "c": column})
    return result.scalar()


def upgrade():
    columns_to_add = [
        # General
        ('abbreviation', sa.String(50), None),
        ('color', sa.String(7), '#000000'),
        ('contract_type', sa.String(30), 'full_time'),
        ('days_per_week', sa.Integer(), '5'),
        ('workdays_calculation', sa.String(20), 'flexible'),

        # Work Time Guidelines
        ('daily_hours_min', sa.Float(), None),
        ('daily_hours_target', sa.Float(), None),
        ('daily_hours_max', sa.Float(), None),
        ('weekly_hours_min', sa.Float(), None),
        ('weekly_hours_target', sa.Float(), '40.0'),
        ('monthly_hours_max', sa.Float(), None),

        # Work Hours Per Day
        ('work_hours_mon', sa.String(5), None),
        ('work_hours_tue', sa.String(5), None),
        ('work_hours_wed', sa.String(5), None),
        ('work_hours_thu', sa.String(5), None),
        ('work_hours_fri', sa.String(5), None),
        ('work_hours_sat', sa.String(5), None),
        ('work_hours_sun', sa.String(5), None),

        # AutoScheduler Parameters
        ('use_target_work_times', sa.Boolean(), '0'),
        ('schedule_after_day_off', sa.Boolean(), '0'),
        ('min_days_off_per_week', sa.Integer(), None),
        ('min_consec_days_off_week', sa.Integer(), None),
        ('max_consecutive_days_off', sa.Integer(), None),
        ('max_consecutive_days', sa.Integer(), '7'),
        ('min_days_per_week', sa.Integer(), None),
        ('max_days_per_week', sa.Integer(), '6'),
        ('weeks_max_1_sat', sa.Integer(), None),
        ('min_rest_hours', sa.Float(), '10.0'),

        # Scheduling Parameters
        ('max_saturdays_per_month', sa.Integer(), None),
        ('max_sundays_per_month', sa.Integer(), None),
        ('min_net_work_hours_day', sa.String(5), None),
        ('max_net_work_hours_day', sa.String(5), None),
        ('rest_between_workdays', sa.String(5), None),
        ('max_activity_duration', sa.String(5), None),
        ('exclude_illness', sa.Boolean(), '0'),
        ('exclude_vacation', sa.Boolean(), '0'),
        ('min_gap_between_activities', sa.String(5), None),
        ('max_gap_between_activities', sa.String(5), None),
        ('max_shifts_per_day', sa.Integer(), None),
        ('max_work_hours_per_day_flag', sa.Boolean(), '0'),
        ('max_work_hours_include_activities', sa.Boolean(), '0'),
        ('max_work_hours_include_day_models', sa.Boolean(), '0'),
        ('min_weekends_off_month', sa.Integer(), None),
        ('max_working_days_per_week', sa.Integer(), None),
        ('max_consecutive_working_days', sa.Integer(), None),
        ('exclude_illness_consec', sa.Boolean(), '0'),
        ('exclude_vacation_consec', sa.Boolean(), '0'),
        ('min_consec_days_off_week_sched', sa.Integer(), None),
        ('max_work_hours_24h', sa.String(5), None),
        ('min_days_off_sat_work', sa.Integer(), None),
        ('min_days_off_sun_work', sa.Integer(), None),
        ('overtime_threshold_consec', sa.String(5), None),
        ('overtime_num_weeks', sa.Integer(), None),
        ('no_schedule_on_holidays', sa.Boolean(), '0'),
        ('max_night_shifts_week', sa.Integer(), None),
        ('max_night_shifts_month', sa.Integer(), None),
        ('max_consec_night_shifts', sa.Integer(), None),
        ('weekly_rest_no_full_day', sa.String(5), None),
        ('weekly_rest_full_day', sa.String(5), None),
        ('avoid_overlap_sun_rule', sa.Boolean(), '0'),
        ('max_activity_duration_2', sa.String(5), None),
        ('max_sun_holidays_month', sa.Integer(), None),
        ('comp_eligibility_weekend', sa.Integer(), None),
        ('rest_after_holiday_no_full', sa.String(5), None),
        ('rest_after_holiday_full', sa.String(5), None),
        ('max_sundays_in_row', sa.Integer(), None),
        ('max_weekends_in_row', sa.Integer(), None),
        ('max_day_models_24h', sa.Integer(), None),
        ('max_work_time_deviation', sa.String(5), None),

        # Legacy (may already exist from db.create_all)
        ('break_duration_mins', sa.Integer(), '30'),
        ('break_after_hours', sa.Float(), '4.0'),
        ('lunch_duration_mins', sa.Integer(), '30'),
        ('overtime_eligible', sa.Boolean(), '1'),
        ('is_active', sa.Boolean(), '1'),
    ]

    with op.batch_alter_table('contracts', schema=None) as batch_op:
        for col_name, col_type, default in columns_to_add:
            if not _column_exists('contracts', col_name):
                batch_op.add_column(sa.Column(col_name, col_type, server_default=default, nullable=True))


def downgrade():
    new_cols = [
        'abbreviation', 'color', 'contract_type', 'days_per_week', 'workdays_calculation',
        'daily_hours_min', 'daily_hours_target', 'daily_hours_max',
        'weekly_hours_min', 'weekly_hours_target', 'monthly_hours_max',
        'work_hours_mon', 'work_hours_tue', 'work_hours_wed', 'work_hours_thu',
        'work_hours_fri', 'work_hours_sat', 'work_hours_sun',
        'use_target_work_times', 'schedule_after_day_off', 'min_days_off_per_week',
        'min_consec_days_off_week', 'max_consecutive_days_off', 'max_consecutive_days',
        'min_days_per_week', 'max_days_per_week', 'weeks_max_1_sat', 'min_rest_hours',
        'max_saturdays_per_month', 'max_sundays_per_month',
        'min_net_work_hours_day', 'max_net_work_hours_day',
        'rest_between_workdays', 'max_activity_duration',
        'exclude_illness', 'exclude_vacation',
        'min_gap_between_activities', 'max_gap_between_activities',
        'max_shifts_per_day', 'max_work_hours_per_day_flag',
        'max_work_hours_include_activities', 'max_work_hours_include_day_models',
        'min_weekends_off_month', 'max_working_days_per_week', 'max_consecutive_working_days',
        'exclude_illness_consec', 'exclude_vacation_consec', 'min_consec_days_off_week_sched',
        'max_work_hours_24h', 'min_days_off_sat_work', 'min_days_off_sun_work',
        'overtime_threshold_consec', 'overtime_num_weeks', 'no_schedule_on_holidays',
        'max_night_shifts_week', 'max_night_shifts_month', 'max_consec_night_shifts',
        'weekly_rest_no_full_day', 'weekly_rest_full_day', 'avoid_overlap_sun_rule',
        'max_activity_duration_2', 'max_sun_holidays_month', 'comp_eligibility_weekend',
        'rest_after_holiday_no_full', 'rest_after_holiday_full',
        'max_sundays_in_row', 'max_weekends_in_row', 'max_day_models_24h',
        'max_work_time_deviation',
    ]
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        for col in new_cols:
            batch_op.drop_column(col)
