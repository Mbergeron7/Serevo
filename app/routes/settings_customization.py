"""Settings — Customization: segment codes, shifts, LOB settings, time-off, rules, holidays, etc."""
import json
import logging

from flask import render_template, request, jsonify
from app.auth import admin_required, get_current_user
from app.routes.settings import settings_bp, _demo_guard

log = logging.getLogger("serevo.settings")

# ═══════════════════════════════════════════════════════════════
# CUSTOMIZATION (Admin-only)
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/customization")
@admin_required
def customization():
    """Main customization page — all admin-configurable settings."""
    user = get_current_user()

    # Demo mode — return mock data instead of querying the DB
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_settings_data
        demo = get_demo_settings_data()
        return render_template("settings/customization.html", user=user, **demo)

    from app.models import (SegmentCode,
                            PlanningUnit, TimeOffType, OvertimeRule, ScheduleRule,
                            AdherenceException, AlertConfig,
                            BrandSetting, LobMapping)
    from app.models import db as _db

    def _safe_query(fn, label="query"):
        try:
            return fn()
        except Exception as e:
            log.warning(f"customization {label} failed: {e}")
            _db.session.rollback()
            return []

    segments = _safe_query(lambda: [s.to_dict() for s in SegmentCode.query.order_by(SegmentCode.sort_order, SegmentCode.label).all()], "segments")
    all_lobs = _safe_query(lambda: [{"id": pu.id, "name": pu.name} for pu in PlanningUnit.get_all_active()], "all_lobs")

    time_off_types = _safe_query(lambda: [t.to_dict() for t in TimeOffType.query.order_by(TimeOffType.sort_order).all()], "time_off_types")
    ot_rules = _safe_query(lambda: [r.to_dict() for r in OvertimeRule.query.order_by(OvertimeRule.name).all()], "ot_rules")
    sched_rules = _safe_query(lambda: [r.to_dict() for r in ScheduleRule.query.order_by(ScheduleRule.name).all()], "sched_rules")
    adherence_codes = _safe_query(lambda: [a.to_dict() for a in AdherenceException.query.order_by(AdherenceException.sort_order).all()], "adherence_codes")
    alerts = _safe_query(lambda: [a.to_dict() for a in AlertConfig.query.order_by(AlertConfig.name).all()], "alerts")
    brand_data = {}
    try:
        brand = BrandSetting.query.first()
        brand_data = brand.to_dict() if brand else {}
    except Exception:
        _db.session.rollback()
    lob_mappings = _safe_query(lambda: [{"id": m.id, "source_name": m.source_name,
                     "planning_unit_name": m.planning_unit_name}
                    for m in LobMapping.query.order_by(LobMapping.source_name).all()], "lob_mappings")

    return render_template("settings/customization.html",
        user=user,
        segments=segments, all_lobs=all_lobs,
        time_off_types=time_off_types, ot_rules=ot_rules,
        sched_rules=sched_rules,
        adherence_codes=adherence_codes,
        alerts=alerts, brand_data=brand_data,
        lob_mappings=lob_mappings,
    )


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")
    return None


# ── Segment Codes CRUD ───────────────────────────────────────

@settings_bp.route("/customization/segments/save", methods=["POST"])
@admin_required
def save_segment():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SegmentCode
    data = request.get_json(silent=True) or {}
    seg_id = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if seg_id:
        seg = SegmentCode.query.get(seg_id)
        if not seg:
            return jsonify({"success": False, "error": "Segment not found"})
        seg.code = code
        seg.label = label
        seg.color = data.get("color", seg.color)
        seg.is_productive = bool(data.get("is_productive", seg.is_productive))
        seg.is_paid = bool(data.get("is_paid", seg.is_paid))
        seg.sort_order = int(data.get("sort_order", seg.sort_order))
        seg.offset_mins = data.get("offset_mins") if data.get("offset_mins") is not None else seg.offset_mins
        seg.duration_mins = data.get("duration_mins") if data.get("duration_mins") is not None else seg.duration_mins
        seg.is_flexible = bool(data.get("is_flexible", seg.is_flexible))
        seg.window_start_mins = data.get("window_start_mins") if data.get("window_start_mins") is not None else seg.window_start_mins
        seg.window_end_mins = data.get("window_end_mins") if data.get("window_end_mins") is not None else seg.window_end_mins
    else:
        if SegmentCode.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        seg = SegmentCode(
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
        )
        db.session.add(seg)
    db.session.commit()
    return jsonify({"success": True, "segment": seg.to_dict()})


@settings_bp.route("/customization/segments/delete", methods=["POST"])
@admin_required
def delete_segment():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SegmentCode
    data = request.get_json(silent=True) or {}
    seg = SegmentCode.query.get(data.get("id"))
    if not seg:
        return jsonify({"success": False, "error": "Segment not found"})
    if seg.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default segment code"})
    db.session.delete(seg)
    db.session.commit()
    return jsonify({"success": True})


# ── Shift Templates CRUD ─────────────────────────────────────

@settings_bp.route("/customization/shifts/save", methods=["POST"])
@admin_required
def save_shift():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ShiftTemplate
    data = request.get_json(silent=True) or {}
    shift_id = data.get("id")
    name = (data.get("name") or "").strip()
    start_time = (data.get("start_time") or "").strip()
    end_time = (data.get("end_time") or "").strip()
    if not name or not start_time or not end_time:
        return jsonify({"success": False, "error": "Name, start time, and end time are required"})

    segments_json = json.dumps(data.get("segments", []))

    pu_id = data.get("planning_unit_id") or None
    if pu_id:
        pu_id = int(pu_id)
    day_type = data.get("day_type", "any") or "any"
    shift_category = data.get("shift_category", "any") or "any"

    if shift_id:
        shift = ShiftTemplate.query.get(shift_id)
        if not shift:
            return jsonify({"success": False, "error": "Shift template not found"})
        shift.name = name
        shift.start_time = start_time
        shift.end_time = end_time
        shift.hours = float(data.get("hours", shift.hours))
        shift.shift_type = data.get("shift_type", shift.shift_type)
        shift.segments_json = segments_json
        shift.sort_order = int(data.get("sort_order", shift.sort_order))
        shift.planning_unit_id = pu_id
        shift.day_type = day_type
        shift.shift_category = shift_category
    else:
        shift = ShiftTemplate(
            name=name, start_time=start_time, end_time=end_time,
            hours=float(data.get("hours", 8.0)),
            shift_type=data.get("shift_type", "full"),
            segments_json=segments_json,
            sort_order=int(data.get("sort_order", 0)),
            planning_unit_id=pu_id,
            day_type=day_type,
            shift_category=shift_category,
        )
        db.session.add(shift)
    db.session.commit()
    return jsonify({"success": True, "shift": shift.to_dict()})


@settings_bp.route("/customization/shifts/delete", methods=["POST"])
@admin_required
def delete_shift():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ShiftTemplate
    data = request.get_json(silent=True) or {}
    shift = ShiftTemplate.query.get(data.get("id"))
    if not shift:
        return jsonify({"success": False, "error": "Shift template not found"})
    db.session.delete(shift)
    db.session.commit()
    return jsonify({"success": True})



# ── LOB Settings CRUD ────────────────────────────────────────

@settings_bp.route("/customization/lob-settings/save", methods=["POST"])
@admin_required
def save_lob_setting():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LOBSetting
    data = request.get_json(silent=True) or {}
    setting_id = data.get("id")
    pu_id = data.get("planning_unit_id")

    if setting_id:
        setting = LOBSetting.query.get(setting_id)
        if not setting:
            return jsonify({"success": False, "error": "LOB setting not found"})
    else:
        if not pu_id:
            return jsonify({"success": False, "error": "Planning unit is required"})
        existing = LOBSetting.query.filter_by(planning_unit_id=pu_id).first()
        if existing:
            setting = existing
        else:
            setting = LOBSetting(planning_unit_id=pu_id)
            db.session.add(setting)

    # Update fields
    for field in ["service_level_target", "target_asa", "shrinkage_pct",
                  "occupancy_target", "max_occupancy", "default_shift_hrs"]:
        if field in data:
            setattr(setting, field, float(data[field]))
    if "interval_minutes" in data:
        setting.interval_minutes = int(data["interval_minutes"])
    for field in ["operating_start", "operating_end",
                  "sat_operating_start", "sat_operating_end",
                  "sun_operating_start", "sun_operating_end"]:
        if field in data:
            val = (data[field] or "").strip()
            setattr(setting, field, val if val else None)

    db.session.commit()
    return jsonify({"success": True, "setting": setting.to_dict()})


@settings_bp.route("/customization/lob-settings/delete", methods=["POST"])
@admin_required
def delete_lob_setting():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LOBSetting
    data = request.get_json(silent=True) or {}
    setting = LOBSetting.query.get(data.get("id"))
    if not setting:
        return jsonify({"success": False, "error": "LOB setting not found"})
    db.session.delete(setting)
    db.session.commit()
    return jsonify({"success": True})


# ── Time-Off Types CRUD ──────────────────────────────────────

@settings_bp.route("/customization/time-off-types/save", methods=["POST"])
@admin_required
def save_time_off_type():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, TimeOffType
    data = request.get_json(silent=True) or {}
    tid = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if tid:
        t = TimeOffType.query.get(tid)
        if not t:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if TimeOffType.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        t = TimeOffType(code=code)
        db.session.add(t)

    t.code = code; t.label = label
    t.color = data.get("color", t.color if tid else "#6b7280")
    t.is_paid = bool(data.get("is_paid", True))
    t.requires_approval = bool(data.get("requires_approval", True))
    t.max_days_per_year = data.get("max_days_per_year") or None
    t.min_notice_days = int(data.get("min_notice_days", 0))
    t.sort_order = int(data.get("sort_order", 0))
    db.session.commit()
    return jsonify({"success": True, "item": t.to_dict()})


@settings_bp.route("/customization/time-off-types/delete", methods=["POST"])
@admin_required
def delete_time_off_type():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, TimeOffType
    data = request.get_json(silent=True) or {}
    t = TimeOffType.query.get(data.get("id"))
    if not t:
        return jsonify({"success": False, "error": "Not found"})
    if t.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default type"})
    db.session.delete(t)
    db.session.commit()
    return jsonify({"success": True})


# ── Overtime Rules CRUD ──────────────────────────────────────

@settings_bp.route("/customization/overtime-rules/save", methods=["POST"])
@admin_required
def save_overtime_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, OvertimeRule
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if rid:
        r = OvertimeRule.query.get(rid)
        if not r:
            return jsonify({"success": False, "error": "Not found"})
    else:
        r = OvertimeRule(name=name)
        db.session.add(r)

    r.name = name
    r.rule_type = data.get("rule_type", r.rule_type if rid else "voluntary")
    r.max_ot_hours_week = float(data.get("max_ot_hours_week", 10))
    r.max_ot_hours_day = float(data.get("max_ot_hours_day", 4))
    r.requires_approval = bool(data.get("requires_approval", True))
    r.min_notice_hours = int(data.get("min_notice_hours", 24))
    r.eligible_after_days = int(data.get("eligible_after_days", 90))
    r.pay_multiplier = float(data.get("pay_multiplier", 1.5))
    r.blackout_dates_json = json.dumps(data.get("blackout_dates", []))
    db.session.commit()
    return jsonify({"success": True, "item": r.to_dict()})


@settings_bp.route("/customization/overtime-rules/delete", methods=["POST"])
@admin_required
def delete_overtime_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, OvertimeRule
    data = request.get_json(silent=True) or {}
    r = OvertimeRule.query.get(data.get("id"))
    if not r:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(r)
    db.session.commit()
    return jsonify({"success": True})


# ── Schedule Rules CRUD ──────────────────────────────────────

@settings_bp.route("/customization/schedule-rules/save", methods=["POST"])
@admin_required
def save_schedule_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ScheduleRule
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if rid:
        r = ScheduleRule.query.get(rid)
        if not r:
            return jsonify({"success": False, "error": "Not found"})
    else:
        r = ScheduleRule(name=name)
        db.session.add(r)

    r.name = name
    r.min_hours_week = float(data.get("min_hours_week", 20))
    r.max_hours_week = float(data.get("max_hours_week", 40))
    r.max_hours_day = float(data.get("max_hours_day", 10))
    r.max_consecutive_days = int(data.get("max_consecutive_days", 6))
    r.min_rest_between_shifts_hrs = float(data.get("min_rest_between_shifts_hrs", 10))
    r.min_days_off_per_week = int(data.get("min_days_off_per_week", 1))
    r.max_split_shifts_week = int(data.get("max_split_shifts_week", 0))
    r.allow_back_to_back = bool(data.get("allow_back_to_back", False))
    db.session.commit()
    return jsonify({"success": True, "item": r.to_dict()})


@settings_bp.route("/customization/schedule-rules/delete", methods=["POST"])
@admin_required
def delete_schedule_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ScheduleRule
    data = request.get_json(silent=True) or {}
    r = ScheduleRule.query.get(data.get("id"))
    if not r:
        return jsonify({"success": False, "error": "Not found"})
    if r.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default rule"})
    db.session.delete(r)
    db.session.commit()
    return jsonify({"success": True})


# ── Holidays CRUD ────────────────────────────────────────────

@settings_bp.route("/customization/holidays/save", methods=["POST"])
@admin_required
def save_holiday():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, Holiday
    data = request.get_json(silent=True) or {}
    hid = data.get("id")
    name = (data.get("name") or "").strip()
    date_str = (data.get("date") or "").strip()
    if not name or not date_str:
        return jsonify({"success": False, "error": "Name and date are required"})

    from datetime import datetime as dt
    hdate = dt.strptime(date_str, "%Y-%m-%d").date()

    if hid:
        h = Holiday.query.get(hid)
        if not h:
            return jsonify({"success": False, "error": "Not found"})
    else:
        h = Holiday(name=name, date=hdate, year=hdate.year)
        db.session.add(h)

    h.name = name; h.date = hdate; h.year = hdate.year
    h.is_full_day = bool(data.get("is_full_day", True))
    h.start_time = data.get("start_time") or None
    h.end_time = data.get("end_time") or None
    h.is_paid = bool(data.get("is_paid", True))
    h.affects_forecast = bool(data.get("affects_forecast", True))
    h.volume_factor = float(data.get("volume_factor", 0.0))
    h.is_recurring = bool(data.get("is_recurring", True))
    db.session.commit()
    return jsonify({"success": True, "item": h.to_dict()})


@settings_bp.route("/customization/holidays/delete", methods=["POST"])
@admin_required
def delete_holiday():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, Holiday
    data = request.get_json(silent=True) or {}
    h = Holiday.query.get(data.get("id"))
    if not h:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(h)
    db.session.commit()
    return jsonify({"success": True})


# ── Skill Groups CRUD ───────────────────────────────────────

@settings_bp.route("/customization/skill-groups/save", methods=["POST"])
@admin_required
def save_skill_group():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SkillGroup
    data = request.get_json(silent=True) or {}
    gid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if gid:
        g = SkillGroup.query.get(gid)
        if not g:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if SkillGroup.query.filter_by(name=name).first():
            return jsonify({"success": False, "error": f"'{name}' already exists"})
        g = SkillGroup(name=name)
        db.session.add(g)

    old_name = g.name if gid else None
    g.name = name
    g.description = (data.get("description") or "").strip()

    # Cascade rename to employees assigned to this skill group
    if old_name and old_name != name:
        from app.models import Employee, SkillMapping, PlanningUnit
        # Update all_skills text field for employees with this skill mapping
        mapped_emp_ids = [m.employee_id for m in
                         SkillMapping.query.filter_by(skill_group_id=g.id).all()]
        if mapped_emp_ids:
            for emp in Employee.query.filter(Employee.id.in_(mapped_emp_ids)).all():
                if emp.all_skills:
                    skills = [s.strip() for s in emp.all_skills.split(",")]
                    skills = [name if s == old_name else s for s in skills]
                    emp.all_skills = ", ".join(skills)
                # Update planning_unit_id if it pointed at the old name
                if emp.planning_unit and emp.planning_unit.name == old_name:
                    new_pu = PlanningUnit.query.filter_by(name=name).first()
                    if new_pu:
                        emp.planning_unit_id = new_pu.id

        # Rename the PlanningUnit itself if one matches the old name
        old_pu = PlanningUnit.query.filter_by(name=old_name).first()
        if old_pu:
            existing_new = PlanningUnit.query.filter_by(name=name).first()
            if not existing_new:
                old_pu.name = name
            else:
                # Merge: reassign employees from old PU to existing new PU
                Employee.query.filter_by(planning_unit_id=old_pu.id).update(
                    {"planning_unit_id": existing_new.id})

        # Update LobMapping targets that reference the old name
        from app.models import LobMapping
        LobMapping.query.filter_by(planning_unit_name=old_name).update(
            {"planning_unit_name": name})

    db.session.commit()
    return jsonify({"success": True, "item": g.to_dict()})


@settings_bp.route("/customization/skill-groups/delete", methods=["POST"])
@admin_required
def delete_skill_group():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SkillGroup
    data = request.get_json(silent=True) or {}
    g = SkillGroup.query.get(data.get("id"))
    if not g:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(g)
    db.session.commit()
    return jsonify({"success": True})


# ── Adherence Exception Codes CRUD ───────────────────────────

@settings_bp.route("/customization/adherence-exceptions/save", methods=["POST"])
@admin_required
def save_adherence_exception():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AdherenceException
    data = request.get_json(silent=True) or {}
    aid = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if aid:
        a = AdherenceException.query.get(aid)
        if not a:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if AdherenceException.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        a = AdherenceException(code=code)
        db.session.add(a)

    a.code = code; a.label = label
    a.color = data.get("color", a.color if aid else "#ef4444")
    a.is_excused = bool(data.get("is_excused", False))
    a.category = data.get("category", "other")
    a.sort_order = int(data.get("sort_order", 0))
    db.session.commit()
    return jsonify({"success": True, "item": a.to_dict()})


@settings_bp.route("/customization/adherence-exceptions/delete", methods=["POST"])
@admin_required
def delete_adherence_exception():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AdherenceException
    data = request.get_json(silent=True) or {}
    a = AdherenceException.query.get(data.get("id"))
    if not a:
        return jsonify({"success": False, "error": "Not found"})
    if a.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default code"})
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


# ── Alert Configs CRUD ───────────────────────────────────────

@settings_bp.route("/customization/alerts/save", methods=["POST"])
@admin_required
def save_alert_config():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AlertConfig
    data = request.get_json(silent=True) or {}
    aid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if aid:
        a = AlertConfig.query.get(aid)
        if not a:
            return jsonify({"success": False, "error": "Not found"})
    else:
        a = AlertConfig(name=name, alert_type=data.get("alert_type", "sl_breach"))
        db.session.add(a)

    a.name = name
    a.alert_type = data.get("alert_type", a.alert_type)
    a.threshold_value = float(data.get("threshold_value", 0.80))
    a.threshold_operator = data.get("threshold_operator", "lt")
    a.planning_unit_id = data.get("planning_unit_id") or None
    a.notify_email = bool(data.get("notify_email", False))
    a.notify_in_app = bool(data.get("notify_in_app", True))
    a.email_recipients = (data.get("email_recipients") or "").strip()
    a.cooldown_minutes = int(data.get("cooldown_minutes", 30))
    a.is_active = bool(data.get("is_active", True))
    db.session.commit()
    return jsonify({"success": True, "item": a.to_dict()})


@settings_bp.route("/customization/alerts/delete", methods=["POST"])
@admin_required
def delete_alert_config():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AlertConfig
    data = request.get_json(silent=True) or {}
    a = AlertConfig.query.get(data.get("id"))
    if not a:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


# ── Branding Settings ────────────────────────────────────────

@settings_bp.route("/customization/branding/save", methods=["POST"])
@admin_required
def save_branding():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, BrandSetting
    data = request.get_json(silent=True) or {}

    brand = BrandSetting.query.first()
    if not brand:
        brand = BrandSetting()
        db.session.add(brand)

    brand.company_name = (data.get("company_name") or "").strip()
    brand.tagline = (data.get("tagline") or "").strip()
    brand.accent_color = data.get("accent_color", "#2563eb")
    brand.contact_email = (data.get("contact_email") or "").strip()
    brand.logo_url = (data.get("logo_url") or "").strip()
    brand.favicon_url = (data.get("favicon_url") or "").strip()
    brand.footer_text = (data.get("footer_text") or "").strip()
    db.session.commit()
    return jsonify({"success": True, "brand": brand.to_dict()})


# ── LOB Mappings CRUD ──────────────────────────────────────────

@settings_bp.route("/customization/lob-mappings/save", methods=["POST"])
@admin_required
def save_lob_mapping():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LobMapping, PlanningUnit
    data = request.get_json(silent=True) or {}
    mid = data.get("id")
    source = (data.get("source_name") or "").strip()
    target = (data.get("planning_unit_name") or "").strip()
    if not source or not target:
        return jsonify({"success": False, "error": "Both source name and planning unit are required"})

    if mid:
        m = LobMapping.query.get(mid)
        if not m:
            return jsonify({"success": False, "error": "Not found"})
    else:
        existing = LobMapping.query.filter_by(source_name=source).first()
        if existing:
            return jsonify({"success": False, "error": f"'{source}' is already mapped"})
        m = LobMapping(source_name=source)
        db.session.add(m)

    m.source_name = source
    m.planning_unit_name = target
    # Resolve FK from name
    pu = PlanningUnit.query.filter_by(name=target).first()
    if pu:
        m.planning_unit_id = pu.id
    db.session.commit()
    return jsonify({"success": True, "item": {
        "id": m.id, "source_name": m.source_name,
        "planning_unit_name": m.planning_unit_name,
    }})


@settings_bp.route("/customization/lob-mappings/delete", methods=["POST"])
@admin_required
def delete_lob_mapping():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LobMapping
    data = request.get_json(silent=True) or {}
    m = LobMapping.query.get(data.get("id"))
    if not m:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(m)
    db.session.commit()
    return jsonify({"success": True})

