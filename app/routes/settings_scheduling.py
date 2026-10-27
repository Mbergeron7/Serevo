"""Settings — Scheduling: activities and contracts CRUD."""
import json
import logging

from flask import render_template, request, jsonify
from app.auth import admin_required, get_current_user
from app.routes.settings import settings_bp, _demo_guard

log = logging.getLogger("serevo.settings")

# ═══════════════════════════════════════════════════════════════
# ACTIVITIES — helpers
# ═══════════════════════════════════════════════════════════════

def _save_activity_skills(segment_code, skills_data):
    """Sync skills for an activity (replace all)."""
    from app.models import ActivitySkill, db
    # Remove existing
    ActivitySkill.query.filter_by(segment_code_id=segment_code.id).delete()
    for sk in (skills_data or []):
        name = (sk.get("name") or "").strip()
        if not name:
            continue
        db.session.add(ActivitySkill(
            segment_code_id=segment_code.id,
            name=name,
            weighting=int(sk.get("weighting", 100)),
        ))


def _save_external_status_mappings(segment_code, mappings_data):
    """Sync external status mappings for an activity (replace all)."""
    from app.models import ExternalStatusMapping, db
    ExternalStatusMapping.query.filter_by(segment_code_id=segment_code.id).delete()
    for m in (mappings_data or []):
        ext = (m.get("external_status") or "").strip()
        if not ext:
            continue
        db.session.add(ExternalStatusMapping(
            segment_code_id=segment_code.id,
            external_status=ext,
            description=(m.get("description") or "").strip() or None,
        ))


# ═══════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/activities")
@admin_required
def activities():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_activities
        return render_template("settings/activities.html", items=get_demo_activities())
    from app.models import SegmentCode
    try:
        all_items = SegmentCode.query.filter_by(is_active=True).order_by(SegmentCode.sort_order, SegmentCode.label).all()
        items = [s.to_dict() for s in all_items]
        parents = [{"id": s.id, "label": s.label} for s in all_items if s.is_multi_activity]
    except Exception as e:
        log.warning(f"activities query failed: {e}")
        from app.models import db
        db.session.rollback()
        items, parents = [], []
    return render_template("settings/activities.html", items=items, parents=parents)


@settings_bp.route("/activities/save", methods=["POST"])
@admin_required
def activities_save():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import SegmentCode, db
    data = request.json or {}
    try:
        label = (data.get("label") or "").strip()
        code = (data.get("code") or "").strip().lower().replace(" ", "_")
        if not code and label:
            code = label.lower().replace(" ", "_")
            code = "".join(c for c in code if c.isalnum() or c == "_").strip("_")
        if not code or not label:
            return jsonify(success=False, error="Code and label are required")

        # Parse external IDs from comma-separated string or list
        raw_ext = data.get("external_ids", [])
        if isinstance(raw_ext, str):
            ext_ids = [x.strip() for x in raw_ext.split(",") if x.strip()]
        elif isinstance(raw_ext, list):
            ext_ids = [str(x).strip() for x in raw_ext if str(x).strip()]
        else:
            ext_ids = []

        parent_id = data.get("parent_id")
        if parent_id:
            parent_id = int(parent_id)
        else:
            parent_id = None
        is_multi = bool(data.get("is_multi_activity", False))
        activity_category = data.get("activity_category", "status")
        if activity_category not in ("status", "lob"):
            activity_category = "status"

        # Parse activity type
        activity_type = data.get("activity_type", "presence")
        if activity_type not in ("presence", "break", "absence", "meeting", "vacation"):
            activity_type = "presence"

        seg_id = data.get("id")
        if seg_id:
            item = SegmentCode.query.get(int(seg_id))
            if not item:
                return jsonify(success=False, error="Segment not found")
            # Cascade code rename to existing schedule segments
            old_code = item.code
            if code != old_code:
                from app.models import ShiftSegment
                ShiftSegment.query.filter_by(activity_type=old_code).update(
                    {"activity_type": code}, synchronize_session=False)
            item.code = code
            item.label = label
            item.color = data.get("color", item.color)
            item.is_productive = bool(data.get("is_productive", item.is_productive))
            item.is_paid = bool(data.get("is_paid", item.is_paid))
            item.sort_order = int(data.get("sort_order", item.sort_order))
            item.offset_mins = data.get("offset_mins")
            item.duration_mins = data.get("duration_mins")
            item.is_flexible = bool(data.get("is_flexible", False))
            item.window_start_mins = data.get("window_start_mins")
            item.window_end_mins = data.get("window_end_mins")
            item.activity_category = activity_category
            item.activity_type = activity_type
            item.official_name = (data.get("official_name") or "").strip() or None
            item.abbreviation = (data.get("abbreviation") or "").strip() or None
            item.shortcut = (data.get("shortcut") or "").strip() or None
            item.is_replaceable = bool(data.get("is_replaceable", True))
            item.is_plannable = bool(data.get("is_plannable", True))
            item.importance = int(data.get("importance", 50))
            item.priority = int(data.get("priority", 50))
            item.comply_rest_period = bool(data.get("comply_rest_period", True))
            item.allow_overstaffing_zero = bool(data.get("allow_overstaffing_zero", False))
            item.is_requestable = bool(data.get("is_requestable", False))
            item.is_exchangeable = bool(data.get("is_exchangeable", False))
            item.allow_full_day = bool(data.get("allow_full_day", False))
            item.special_handling = bool(data.get("special_handling", False))
            item.can_be_day_status = bool(data.get("can_be_day_status", False))
            item.set_external_ids(ext_ids)
            item.parent_id = parent_id
            item.is_multi_activity = is_multi
        else:
            if SegmentCode.query.filter_by(code=code).first():
                return jsonify(success=False, error=f"Code '{code}' already exists")
            item = SegmentCode(
                code=code, label=label,
                color=data.get("color", "#6b7280"),
                is_productive=bool(data.get("is_productive", True)),
                is_paid=bool(data.get("is_paid", True)),
                sort_order=int(data.get("sort_order", 0)),
                offset_mins=data.get("offset_mins"),
                duration_mins=data.get("duration_mins"),
                is_flexible=bool(data.get("is_flexible", False)),
                window_start_mins=data.get("window_start_mins"),
                window_end_mins=data.get("window_end_mins"),
                activity_category=activity_category,
                activity_type=activity_type,
                official_name=(data.get("official_name") or "").strip() or None,
                abbreviation=(data.get("abbreviation") or "").strip() or None,
                shortcut=(data.get("shortcut") or "").strip() or None,
                is_replaceable=bool(data.get("is_replaceable", True)),
                is_plannable=bool(data.get("is_plannable", True)),
                importance=int(data.get("importance", 50)),
                priority=int(data.get("priority", 50)),
                comply_rest_period=bool(data.get("comply_rest_period", True)),
                allow_overstaffing_zero=bool(data.get("allow_overstaffing_zero", False)),
                is_requestable=bool(data.get("is_requestable", False)),
                is_exchangeable=bool(data.get("is_exchangeable", False)),
                allow_full_day=bool(data.get("allow_full_day", False)),
                special_handling=bool(data.get("special_handling", False)),
                can_be_day_status=bool(data.get("can_be_day_status", False)),
                parent_id=parent_id,
                is_multi_activity=is_multi,
            )
            item.set_external_ids(ext_ids)
            db.session.add(item)

        # Handle skills
        db.session.flush()  # ensure item.id is set
        _save_activity_skills(item, data.get("skills", []))
        _save_external_status_mappings(item, data.get("external_statuses", []))
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/activities/delete", methods=["POST"])
@admin_required
def activities_delete():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import SegmentCode, db
    d = request.json or {}
    try:
        item = SegmentCode.query.get(int(d["id"]))
        if not item:
            return jsonify(success=False, error="Segment not found")
        if item.is_default:
            return jsonify(success=False, error="Cannot delete a default segment code")
        # Reassign any schedule segments using this code to the default activity
        from app.models import ShiftSegment
        default_code = SegmentCode.query.filter_by(is_default=True).first()
        fallback = default_code.code if default_code else "on-call"
        ShiftSegment.query.filter_by(activity_type=item.code).update(
            {"activity_type": fallback}, synchronize_session=False)
        db.session.delete(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# CONTRACTS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/contracts")
@admin_required
def contracts():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_contracts
        return render_template("settings/contracts.html", items=get_demo_contracts(), schedule_rules=[])
    from app.models import Contract, ScheduleRule
    try:
        items = [c.to_dict() for c in Contract.query.order_by(Contract.name).all()]
        schedule_rules = ScheduleRule.query.filter_by(is_active=True).order_by(ScheduleRule.name).all()
    except Exception as e:
        log.warning(f"contracts query failed: {e}")
        from app.models import db
        db.session.rollback()
        items, schedule_rules = [], []
    return render_template("settings/contracts.html", items=items, schedule_rules=schedule_rules)


@settings_bp.route("/contracts/save", methods=["POST"])
@admin_required
def contracts_save():
    from app.models import Contract, db
    d = request.json or {}
    try:
        if not d.get("name"):
            return jsonify({"success": False, "error": "Name is required"})
        item = Contract.query.get(int(d["id"])) if d.get("id") else Contract()
        item.name = d["name"]

        # String fields
        for fld in ["abbreviation", "color", "contract_type", "workdays_calculation",
                     "work_hours_mon", "work_hours_tue", "work_hours_wed", "work_hours_thu",
                     "work_hours_fri", "work_hours_sat", "work_hours_sun",
                     "min_net_work_hours_day", "max_net_work_hours_day",
                     "rest_between_workdays", "max_activity_duration",
                     "min_gap_between_activities", "max_gap_between_activities",
                     "max_work_hours_24h", "overtime_threshold_consec",
                     "weekly_rest_no_full_day", "weekly_rest_full_day",
                     "max_activity_duration_2", "rest_after_holiday_no_full",
                     "rest_after_holiday_full", "max_work_time_deviation",
                     "break_earliest", "break_latest", "lunch_earliest", "lunch_latest"]:
            if fld in d:
                setattr(item, fld, d[fld] or None)

        # Float fields
        for fld in ["weekly_hours", "daily_hours_min", "daily_hours_target", "daily_hours_max",
                     "weekly_hours_min", "weekly_hours_target", "monthly_hours_max",
                     "min_rest_hours", "break_after_hours"]:
            if fld in d and d[fld] is not None and d[fld] != '':
                setattr(item, fld, float(d[fld]))
            elif fld in d:
                setattr(item, fld, None)

        # Integer fields
        for fld in ["days_per_week", "min_days_per_week", "max_days_per_week",
                     "break_duration_mins", "lunch_duration_mins", "max_consecutive_days",
                     "min_days_off_per_week", "min_consec_days_off_week",
                     "max_consecutive_days_off", "weeks_max_1_sat",
                     "max_saturdays_per_month", "max_sundays_per_month",
                     "max_shifts_per_day", "min_weekends_off_month",
                     "max_working_days_per_week", "max_consecutive_working_days",
                     "min_consec_days_off_week_sched", "min_days_off_sat_work",
                     "min_days_off_sun_work", "overtime_num_weeks",
                     "max_night_shifts_week", "max_night_shifts_month",
                     "max_consec_night_shifts", "max_sun_holidays_month",
                     "comp_eligibility_weekend", "max_sundays_in_row",
                     "max_weekends_in_row", "max_day_models_24h"]:
            if fld in d and d[fld] is not None and d[fld] != '':
                setattr(item, fld, int(d[fld]))
            elif fld in d:
                setattr(item, fld, None)

        # Boolean fields
        for fld in ["overtime_eligible", "is_active", "use_target_work_times", "schedule_after_day_off",
                     "exclude_illness", "exclude_vacation",
                     "max_work_hours_per_day_flag", "max_work_hours_include_activities",
                     "max_work_hours_include_day_models",
                     "exclude_illness_consec", "exclude_vacation_consec",
                     "no_schedule_on_holidays", "avoid_overlap_sun_rule"]:
            if fld in d:
                setattr(item, fld, bool(d[fld]))

        item.schedule_rule_id = int(d["schedule_rule_id"]) if d.get("schedule_rule_id") else None
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/contracts/delete", methods=["POST"])
@admin_required
def contracts_delete():
    from app.models import Contract, db
    d = request.json or {}
    try:
        item = Contract.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))

