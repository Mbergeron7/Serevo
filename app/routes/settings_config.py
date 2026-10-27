"""Settings — Config: day models, planning units, skills, selections, shift sequences, calendars, reset, activity log."""
import json
import logging
from datetime import datetime

from flask import render_template, request, jsonify, redirect, url_for
from app.auth import login_required, admin_required, get_current_user
from app.routes.settings import settings_bp, _demo_guard, _parse_date

log = logging.getLogger("serevo.settings")

# ═══════════════════════════════════════════════════════════════
# DAY MODELS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/day-models")
@admin_required
def day_models():
    """Redirect to Scheduling Config — Day Models now live there."""
    from flask import redirect
    return redirect("/scheduling/config/")


# ═══════════════════════════════════════════════════════════════
# PLANNING UNITS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/planning-units")
@admin_required
def planning_units_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_planning_units_config
        return render_template("settings/planning_units.html", items=get_demo_planning_units_config(), activities=[])
    from app.models import PlanningUnit, Employee, SegmentCode
    try:
        pus = PlanningUnit.query.order_by(PlanningUnit.name).all()
        items = []
        for pu in pus:
            d = pu.to_dict()
            d["employee_count"] = Employee.query.filter_by(planning_unit_id=pu.id).count()
            items.append(d)
        activities = [s.to_dict() for s in SegmentCode.query.filter_by(is_active=True).order_by(SegmentCode.label).all()]
    except Exception as e:
        log.warning(f"planning_units query failed: {e}")
        from app.models import db
        db.session.rollback()
        items, activities = [], []
    return render_template("settings/planning_units.html", items=items, activities=activities)


@settings_bp.route("/planning-units/save", methods=["POST"])
@admin_required
def planning_units_save():
    from app.models import PlanningUnit, PlanningUnitBusinessHours, PlanningUnitActivity, PlanningUnitParameter, CallRoute, LOBSetting, db
    d = request.json or {}
    try:
        if d.get("id"):
            item = PlanningUnit.query.get(int(d["id"]))
            if not item:
                return jsonify(success=False, error="Not found")
        else:
            item = PlanningUnit()
            db.session.add(item)
        item.name = (d.get("name") or "").strip()
        if not item.name:
            return jsonify(success=False, error="Name is required")
        item.description = (d.get("description") or "").strip() or None
        item.timezone = (d.get("timezone") or "America/New_York").strip()
        item.is_active = bool(d.get("is_active", True))
        item.is_multi_activity = bool(d.get("is_multi_activity", False))
        parent_id = d.get("parent_id")
        item.parent_id = int(parent_id) if parent_id else None
        # If this is a multi-activity parent, clear its own parent
        if item.is_multi_activity:
            item.parent_id = None
        db.session.flush()

        # Sync business hours (replace all)
        PlanningUnitBusinessHours.query.filter_by(planning_unit_id=item.id).delete()
        for bh in (d.get("business_hours") or []):
            day_type = (bh.get("day_type") or "").strip()
            if not day_type:
                continue
            db.session.add(PlanningUnitBusinessHours(
                planning_unit_id=item.id,
                day_type=day_type,
                open_time=bh.get("open_time", "08:00"),
                close_time=bh.get("close_time", "22:00"),
                valid_from=_parse_date(bh.get("valid_from")),
                valid_to=_parse_date(bh.get("valid_to")),
            ))

        # ── Sync business hours → LOBSetting so forecast/scheduling respect them ──
        bh_rows = d.get("business_hours") or []
        if bh_rows:
            lob_s = LOBSetting.query.filter_by(planning_unit_id=item.id).first()
            if not lob_s:
                lob_s = LOBSetting(planning_unit_id=item.id)
                db.session.add(lob_s)

            # Derive weekday / saturday / sunday hours from business_hours entries
            day_map = {}  # day_type -> (open, close)
            for bh in bh_rows:
                dt = (bh.get("day_type") or "").strip().lower()
                if dt:
                    day_map[dt] = (bh.get("open_time", "08:00"), bh.get("close_time", "22:00"))

            # Weekday: use monday as representative, fall back to any weekday
            weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday"]
            wk_hours = None
            for wd in weekdays:
                if wd in day_map:
                    wk_hours = day_map[wd]
                    break
            if wk_hours:
                lob_s.operating_start = wk_hours[0]
                lob_s.operating_end = wk_hours[1]

            # Saturday
            if "saturday" in day_map:
                lob_s.sat_operating_start = day_map["saturday"][0]
                lob_s.sat_operating_end = day_map["saturday"][1]
            else:
                lob_s.sat_operating_start = None
                lob_s.sat_operating_end = None

            # Sunday
            if "sunday" in day_map:
                lob_s.sun_operating_start = day_map["sunday"][0]
                lob_s.sun_operating_end = day_map["sunday"][1]
            else:
                lob_s.sun_operating_start = None
                lob_s.sun_operating_end = None

        # Sync assigned activities (replace all)
        PlanningUnitActivity.query.filter_by(planning_unit_id=item.id).delete()
        for pa in (d.get("assigned_activities") or []):
            sc_id = pa.get("segment_code_id")
            if not sc_id:
                continue
            db.session.add(PlanningUnitActivity(
                planning_unit_id=item.id,
                segment_code_id=int(sc_id),
                window_start=pa.get("window_start") or None,
                window_end=pa.get("window_end") or None,
                valid_from=_parse_date(pa.get("valid_from")),
                valid_to=_parse_date(pa.get("valid_to")),
            ))

        # Sync call routes (replace all — flush delete before re-insert
        # so the unique constraint on route_id doesn't fire)
        CallRoute.query.filter_by(planning_unit_id=item.id).delete()
        db.session.flush()
        seen_route_ids = set()
        for cr in (d.get("call_routes") or []):
            route_id = (cr.get("route_id") or "").strip()
            route_name = (cr.get("name") or "").strip()
            if not route_id or not route_name:
                continue
            if route_id in seen_route_ids:
                continue  # skip duplicate route IDs within the same planning unit
            seen_route_ids.add(route_id)
            db.session.add(CallRoute(
                planning_unit_id=item.id,
                route_id=route_id,
                name=route_name,
                is_active=bool(cr.get("is_active", True)),
            ))

        # Sync parameters (replace all)
        PlanningUnitParameter.query.filter_by(planning_unit_id=item.id).delete()
        for p in (d.get("parameters") or []):
            name = (p.get("name") or "").strip()
            if not name:
                continue
            db.session.add(PlanningUnitParameter(
                planning_unit_id=item.id,
                name=name,
                lower_limit=float(p["lower_limit"]) if p.get("lower_limit") not in (None, "") else None,
                upper_limit=float(p["upper_limit"]) if p.get("upper_limit") not in (None, "") else None,
            ))

        db.session.commit()
        return jsonify(success=True, id=item.id)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))



@settings_bp.route("/planning-units/delete", methods=["POST"])
@admin_required
def planning_units_delete():
    from app.models import PlanningUnit, db
    d = request.json or {}
    try:
        pu_id = int(d["id"])
        item = PlanningUnit.query.get(pu_id)
        if item:
            # Clear ALL FK references via raw SQL to avoid loading models
            # with potentially missing columns
            stmts = [
                "UPDATE employees SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
                "DELETE FROM requirement_intervals WHERE planning_unit_id = :pid",
                "DELETE FROM forecast_intervals WHERE planning_unit_id = :pid",
                "DELETE FROM planning_unit_business_hours WHERE planning_unit_id = :pid",
                "DELETE FROM planning_unit_activities WHERE planning_unit_id = :pid",
                "DELETE FROM planning_unit_parameters WHERE planning_unit_id = :pid",
                "DELETE FROM call_routes WHERE planning_unit_id = :pid",
                "DELETE FROM interval_actuals WHERE planning_unit_id = :pid",
                "DELETE FROM forecast_scenarios WHERE planning_unit_id = :pid",
                "UPDATE week_time_patterns SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
                "UPDATE day_models SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
                "UPDATE contracts SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
                "UPDATE shift_sequences SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
                "UPDATE agent_status_events SET planning_unit_id = NULL WHERE planning_unit_id = :pid",
            ]
            for sql in stmts:
                try:
                    db.session.execute(db.text(sql), {"pid": pu_id})
                except Exception:
                    pass  # table may not exist yet
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SKILLS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/skills")
@admin_required
def skills_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_skills_config
        return render_template("settings/skills.html", items=get_demo_skills_config())
    from app.models import SkillGroup, SkillMapping
    try:
        groups = SkillGroup.query.order_by(SkillGroup.name).all()
        items = []
        for g in groups:
            items.append({
                "id": g.id, "name": g.name,
                "description": g.description or "",
                "is_active": g.is_active,
                "mapping_count": SkillMapping.query.filter_by(skill_group_id=g.id).count(),
            })
    except Exception as e:
        log.warning(f"skills query failed: {e}")
        from app.models import db
        db.session.rollback()
        items = []
    return render_template("settings/skills.html", items=items)


@settings_bp.route("/skills/save", methods=["POST"])
@admin_required
def skills_save():
    from app.models import SkillGroup, db
    d = request.json or {}
    try:
        item = SkillGroup.query.get(int(d["id"])) if d.get("id") else SkillGroup()
        item.name = d["name"]
        item.description = d.get("description", "")
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/skills/delete", methods=["POST"])
@admin_required
def skills_delete():
    from app.models import SkillGroup, db
    d = request.json or {}
    try:
        item = SkillGroup.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SELECTIONS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/selections")
@admin_required
def selections_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_selections
        return render_template("settings/selections.html", items=get_demo_selections())
    from app.models import Selection, SelectionMember
    try:
        sels = Selection.query.order_by(Selection.name).all()
        items = []
        for s in sels:
            items.append({
                "id": s.id, "name": s.name,
                "description": s.description or "",
                "is_active": s.is_active,
                "member_count": SelectionMember.query.filter_by(selection_id=s.id).count(),
            })
    except Exception as e:
        log.warning(f"selections query failed: {e}")
        from app.models import db
        db.session.rollback()
        items = []
    return render_template("settings/selections.html", items=items)


@settings_bp.route("/selections/save", methods=["POST"])
@admin_required
def selections_save():
    from app.models import Selection, db
    d = request.json or {}
    try:
        item = Selection.query.get(int(d["id"])) if d.get("id") else Selection()
        item.name = d["name"]
        item.description = d.get("description", "")
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/selections/delete", methods=["POST"])
@admin_required
def selections_delete():
    from app.models import Selection, db
    d = request.json or {}
    try:
        item = Selection.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SHIFT SEQUENCES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/shift-sequences")
@admin_required
def shift_sequences_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_shift_sequences
        data = get_demo_shift_sequences()
        return render_template("settings/shift_sequences.html",
                               items=data["items"],
                               shift_templates=data.get("shift_templates", []),
                               day_models=data.get("day_models", []))
    from app.models import ShiftSequence, ShiftTemplate, DayModel
    items, shift_templates, day_models = [], [], []
    try:
        seqs = ShiftSequence.query.order_by(ShiftSequence.name).all()
        items = [s.to_dict() for s in seqs]
    except Exception as e:
        log.warning(f"shift_sequences query failed: {e}")
        from app.models import db
        db.session.rollback()
    try:
        templates = ShiftTemplate.query.filter_by(is_active=True).order_by(ShiftTemplate.name).all()
        shift_templates = [{"id": t.id, "name": t.name, "start": t.start_time, "end": t.end_time} for t in templates]
    except Exception:
        from app.models import db
        db.session.rollback()
    try:
        dms = DayModel.query.filter_by(is_active=True).order_by(DayModel.sort_order, DayModel.name).all()
        day_models = [{"id": d.id, "name": d.name, "abbreviation": d.abbreviation or "",
                       "start": d.start_time, "end": d.end_time, "color": d.color or "#4472C4"} for d in dms]
    except Exception:
        from app.models import db
        db.session.rollback()
    return render_template("settings/shift_sequences.html",
                           items=items, shift_templates=shift_templates, day_models=day_models)


@settings_bp.route("/shift-sequences/save", methods=["POST"])
@admin_required
def shift_sequences_save():
    import json as _json
    from app.models import ShiftSequence, ShiftSequenceRow, db
    d = request.json or {}
    try:
        is_new = not d.get("id")
        item = ShiftSequence.query.get(int(d["id"])) if not is_new else ShiftSequence()
        item.name = d["name"]
        item.cycle_weeks = int(d.get("cycle_weeks", 1))
        item.is_active = bool(d.get("is_active", True))
        if is_new:
            db.session.add(item)
            db.session.flush()  # get item.id
            # Seed initial rows
            initial_rows = int(d.get("initial_rows", 1))
            for i in range(initial_rows):
                row = ShiftSequenceRow(
                    shift_sequence_id=item.id,
                    name=f"Row {i + 1}",
                    position=i,
                    pattern_json=_json.dumps({}),
                )
                db.session.add(row)
        db.session.commit()
        return jsonify(success=True, id=item.id)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/shift-sequences/delete", methods=["POST"])
@admin_required
def shift_sequences_delete():
    from app.models import ShiftSequence, db
    d = request.json or {}
    try:
        item = ShiftSequence.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/shift-sequences/<int:seq_id>/rows")
@admin_required
def shift_sequence_rows(seq_id):
    from app.models import ShiftSequenceRow
    rows = ShiftSequenceRow.query.filter_by(shift_sequence_id=seq_id)\
           .order_by(ShiftSequenceRow.position).all()
    return jsonify([r.to_dict() for r in rows])


@settings_bp.route("/shift-sequences/save-rows", methods=["POST"])
@admin_required
def shift_sequence_save_rows():
    import json as _json
    from app.models import ShiftSequenceRow, db
    d = request.json or {}
    seq_id = d.get("sequence_id")
    rows_data = d.get("rows", [])
    try:
        # Delete existing rows and recreate
        ShiftSequenceRow.query.filter_by(shift_sequence_id=seq_id).delete()
        for i, rd in enumerate(rows_data):
            row = ShiftSequenceRow(
                shift_sequence_id=seq_id,
                name=rd.get("name", ""),
                position=i,
                pattern_json=_json.dumps(rd.get("pattern", {})),
            )
            db.session.add(row)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# PLANNING CALENDARS & DAY TYPES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/planning-calendars")
@admin_required
def planning_calendars_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_planning_calendars
        data = get_demo_planning_calendars()
        return render_template("settings/planning_calendars.html", day_types=data["day_types"], calendars=data["calendars"])
    from app.models import DayType, PlanningCalendar
    try:
        day_types = [dt.to_dict() for dt in DayType.query.order_by(DayType.name).all()]
        calendars = [c.to_dict() for c in PlanningCalendar.query.order_by(PlanningCalendar.name).all()]
    except Exception as e:
        log.warning(f"planning_calendars query failed: {e}")
        from app.models import db
        db.session.rollback()
        day_types, calendars = [], []
    return render_template("settings/planning_calendars.html",
                           day_types=day_types, calendars=calendars)


@settings_bp.route("/planning-calendars/day-types/save", methods=["POST"])
@admin_required
def day_types_save():
    from app.models import DayType, db
    d = request.json or {}
    try:
        item = DayType.query.get(int(d["id"])) if d.get("id") else DayType()
        item.name = d["name"]
        item.color = d.get("color", "#ef4444")
        item.is_holiday = bool(d.get("is_holiday", False))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/day-types/delete", methods=["POST"])
@admin_required
def day_types_delete():
    from app.models import DayType, db
    d = request.json or {}
    try:
        item = DayType.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/save", methods=["POST"])
@admin_required
def planning_calendars_save():
    from app.models import PlanningCalendar, db
    d = request.json or {}
    try:
        item = PlanningCalendar.query.get(int(d["id"])) if d.get("id") else PlanningCalendar()
        item.name = d["name"]
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/delete", methods=["POST"])
@admin_required
def planning_calendars_delete():
    from app.models import PlanningCalendar, db
    d = request.json or {}
    try:
        item = PlanningCalendar.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/<int:cal_id>/entries")
@admin_required
def calendar_entries_list(cal_id):
    from app.models import CalendarEntry
    entries = CalendarEntry.query.filter_by(calendar_id=cal_id)\
              .order_by(CalendarEntry.date).all()
    return jsonify([e.to_dict() for e in entries])


@settings_bp.route("/planning-calendars/entries/save", methods=["POST"])
@admin_required
def calendar_entries_save():
    from datetime import date as _date
    from app.models import CalendarEntry, db
    d = request.json or {}
    try:
        entry = CalendarEntry(
            calendar_id=int(d["calendar_id"]),
            day_type_id=int(d["day_type_id"]),
            date=_date.fromisoformat(d["date"]),
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/entries/delete", methods=["POST"])
@admin_required
def calendar_entries_delete():
    from app.models import CalendarEntry, db
    d = request.json or {}
    try:
        item = CalendarEntry.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# MASTER RESET
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/reset", methods=["POST"])
@admin_required
def master_reset():
    """Wipe all operational data, API connections, and settings.

    Preserves: user accounts, brand settings.
    Blocked for demo users.
    """
    dg = _demo_guard()
    if dg:
        return dg

    data = request.get_json(silent=True) or {}
    confirm = data.get("confirm", "")
    if confirm != "RESET":
        return jsonify({"success": False,
                        "error": "Type RESET to confirm."})

    from app.models import (
        db, User, Employee, Accommodation, PTOEntry,
        PlanningUnit, ForecastInterval, RequirementInterval,
        Schedule, ShiftSegment, APIConnection, DataUpload, DataSource,
        AppSetting, SegmentCode, ShiftTemplate,
        FillInRule, LOBSetting, TimeOffType,
        OvertimeRule, ScheduleRule, Holiday, SkillGroup, SkillMapping,
        AdherenceException, AlertConfig, EmployeeAvailability,
        IntervalActual, LobMapping, AgentStatusEvent,
        Activity, Contract, DayModel,
        EmployeePlanningUnit, EmployeeWorkTimePattern,
        EmployeeContract, Selection, SelectionMember,
        ShiftSequence, ShiftSequenceRow, EmployeeShiftSequence,
        DayType, PlanningCalendar, CalendarEntry,
        CoachingSession, QualityEvaluation, AnalyticsIntegration,
        ShiftPost, ShiftBid, ShiftSwapRequest, VTOOTPost, VTOOTSignup,
        WeekTimePattern, WorkTimePatternModel,
    )

    log.info("=== MASTER RESET initiated ===")

    # Unlink users from employee records so employees can be deleted
    try:
        User.query.update({User.employee_id: None})
        db.session.flush()
    except Exception as e:
        log.warning(f"Reset: error unlinking user→employee: {e}")
        db.session.rollback()

    # Order matters — delete children before parents
    tables_to_clear = [
        # Child tables first (all FK refs to employees)
        ShiftSegment,
        Accommodation,
        PTOEntry,
        SkillMapping,
        EmployeeAvailability,
        EmployeePlanningUnit,
        EmployeeContract,
        EmployeeWorkTimePattern,
        EmployeeShiftSequence,
        SelectionMember,
        AdherenceException,
        AgentStatusEvent,
        IntervalActual,
        CoachingSession,
        QualityEvaluation,
        ShiftBid,
        ShiftSwapRequest,
        VTOOTSignup,
        VTOOTPost,
        ShiftPost,
        # Main data tables
        Schedule,
        ForecastInterval,
        RequirementInterval,
        Employee,
        PlanningUnit,
        # Config tables
        APIConnection,
        DataUpload,
        DataSource,
        SegmentCode,
        ShiftTemplate,
        FillInRule,
        LOBSetting,
        TimeOffType,
        OvertimeRule,
        ScheduleRule,
        Holiday,
        SkillGroup,
        AlertConfig,
        LobMapping,
        Selection,
        # Planning calendars & shift sequences
        CalendarEntry,
        ShiftSequenceRow,
        PlanningCalendar,
        DayType,
        ShiftSequence,
        # Scheduling config tables
        DayModel,
        Activity,
        Contract,
        WeekTimePattern,
        WorkTimePatternModel,
        AnalyticsIntegration,
    ]

    counts = {}
    for model in tables_to_clear:
        try:
            n = model.query.delete()
            counts[model.__tablename__] = n
        except Exception as e:
            log.warning(f"Reset: error clearing {model.__tablename__}: {e}")
            db.session.rollback()

    # Clear app settings (Google Sheet key, etc.) but keep brand
    try:
        AppSetting.query.delete()
        counts["app_settings"] = "cleared"
    except Exception as e:
        log.warning(f"Reset: error clearing app_settings: {e}")
        db.session.rollback()

    db.session.commit()

    total = sum(v for v in counts.values() if isinstance(v, int))
    log.info(f"=== MASTER RESET complete — {total} rows deleted across {len(counts)} tables ===")

    return jsonify({
        "success": True,
        "message": f"Reset complete. {total} records cleared across {len(counts)} tables.",
        "details": counts,
    })


# ═══════════════════════════════════════════════════════════════
# ACTIVITY LOG
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/activity-log")
@admin_required
def activity_log():
    return render_template("settings/activity_log.html", user=get_current_user())


@settings_bp.route("/activity-log/data", methods=["POST"])
@admin_required
def activity_log_data():
    """Return paginated audit log entries."""
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_audit_log
        entries = get_demo_audit_log()
        return jsonify(success=True, entries=entries, total=len(entries), page=1, per_page=50)
    try:
        from app.models import AuditLog
        payload = request.get_json(silent=True) or {}
        page = int(payload.get("page", 1))
        per_page = min(int(payload.get("per_page", 50)), 100)
        action_filter = payload.get("action", "")

        q = AuditLog.query.order_by(AuditLog.created_at.desc())
        if action_filter:
            q = q.filter(AuditLog.action == action_filter)

        total = q.count()
        entries = q.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "success": True,
            "entries": [e.to_dict() for e in entries],
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
        })
    except Exception as e:
        log.exception("Activity log error")
        return jsonify({"success": False, "error": str(e)})
