"""
routes/scheduling_config.py — Scheduling Configuration blueprint
================================================================
CRUD for the three-tier scheduling hierarchy:
  Day Models → Week Time Patterns → Work Time Pattern Models
Plus employee quartile assignments for KPI-based scheduling.
"""

import json
import logging

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import (db, DayModel, WeekTimePattern, WeekTimePatternDayModel,
                        WorkTimePatternModel, WorkTimePatternModelPattern,
                        EmployeeQuartile, Employee, PlanningUnit, ShiftTemplate,
                        Activity)

log = logging.getLogger("serevo.scheduling_config")

scheduling_config_bp = Blueprint("scheduling_config", __name__,
                                  url_prefix="/scheduling/config")


def _admin_or_supervisor():
    user = get_current_user()
    if not user:
        return None, (jsonify(success=False, error="Unauthorized"), 401)
    if user.get("role") not in ("admin", "supervisor"):
        return None, (jsonify(success=False, error="Forbidden"), 403)
    return user, None


# ═══════════════════════════════════════════════════════════════
# MAIN CONFIG PAGE
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("scheduling/config.html", user=user)


# ═══════════════════════════════════════════════════════════════
# DAY MODELS API
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/api/day-models")
@login_required
def list_day_models():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, items=_demo_day_models())
    items = [dm.to_dict() for dm in DayModel.query.order_by(DayModel.sort_order, DayModel.name).all()]
    return jsonify(success=True, items=items)


@scheduling_config_bp.route("/api/day-models", methods=["POST"])
@login_required
def save_day_model():
    user, err = _admin_or_supervisor()
    if err:
        return err
    d = request.json or {}
    try:
        item = DayModel.query.get(int(d["id"])) if d.get("id") else DayModel()
        item.name = d["name"]
        item.abbreviation = d.get("abbreviation", "")
        item.start_time = d.get("start_time", "08:00")
        item.end_time = d.get("end_time", "16:30")
        item.paid_hours = float(d.get("paid_hours", 8))
        item.total_hours = float(d.get("total_hours", 0)) or item.paid_hours
        item.model_type = d.get("model_type", "Fixed")
        item.color = d.get("color", "#4472C4")
        item.day_type = d.get("day_type", "any")
        item.planning_unit_id = int(d["planning_unit_id"]) if d.get("planning_unit_id") else None
        item.shift_template_id = int(d["shift_template_id"]) if d.get("shift_template_id") else None
        item.activities_json = json.dumps(d.get("activities", []))
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True, item=item.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@scheduling_config_bp.route("/api/day-models/<int:item_id>", methods=["DELETE"])
@login_required
def delete_day_model(item_id):
    user, err = _admin_or_supervisor()
    if err:
        return err
    try:
        item = DayModel.query.get(item_id)
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# WEEK TIME PATTERNS API
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/api/week-time-patterns")
@login_required
def list_week_time_patterns():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, items=_demo_week_time_patterns())
    items = [wtp.to_dict() for wtp in WeekTimePattern.query.order_by(WeekTimePattern.name).all()]
    return jsonify(success=True, items=items)


@scheduling_config_bp.route("/api/week-time-patterns", methods=["POST"])
@login_required
def save_week_time_pattern():
    user, err = _admin_or_supervisor()
    if err:
        return err
    d = request.json or {}
    try:
        item = WeekTimePattern.query.get(int(d["id"])) if d.get("id") else WeekTimePattern()
        item.name = d["name"]
        item.abbreviation = d.get("abbreviation", "")
        item.days_json = json.dumps(d.get("days", {}))
        item.total_hours = float(d.get("total_hours", 40))
        item.max_exception_days = int(d.get("max_exception_days", 0))
        item.planning_unit_id = int(d["planning_unit_id"]) if d.get("planning_unit_id") else None
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.flush()

        # Sync assigned day models
        assigned = d.get("assigned_day_model_ids", [])
        if assigned is not None:
            WeekTimePatternDayModel.query.filter_by(week_time_pattern_id=item.id).delete()
            for pos, dm_id in enumerate(assigned):
                link = WeekTimePatternDayModel(
                    week_time_pattern_id=item.id,
                    day_model_id=int(dm_id),
                    position=pos,
                )
                db.session.add(link)

        db.session.commit()
        return jsonify(success=True, item=item.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@scheduling_config_bp.route("/api/week-time-patterns/<int:item_id>", methods=["DELETE"])
@login_required
def delete_week_time_pattern(item_id):
    user, err = _admin_or_supervisor()
    if err:
        return err
    try:
        item = WeekTimePattern.query.get(item_id)
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# WORK TIME PATTERN MODELS API
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/api/work-time-pattern-models")
@login_required
def list_work_time_pattern_models():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, items=_demo_work_time_pattern_models())
    items = [wtpm.to_dict() for wtpm in WorkTimePatternModel.query.order_by(WorkTimePatternModel.name).all()]
    return jsonify(success=True, items=items)


@scheduling_config_bp.route("/api/work-time-pattern-models", methods=["POST"])
@login_required
def save_work_time_pattern_model():
    user, err = _admin_or_supervisor()
    if err:
        return err
    d = request.json or {}
    try:
        item = WorkTimePatternModel.query.get(int(d["id"])) if d.get("id") else WorkTimePatternModel()
        item.name = d["name"]
        item.abbreviation = d.get("abbreviation", "")
        item.model_type = d.get("model_type", "Fixed")
        item.description = d.get("description", "")
        item.patterns_json = json.dumps(d.get("patterns", []))
        item.exception_wtp_id = int(d["exception_wtp_id"]) if d.get("exception_wtp_id") else None
        item.planning_unit_id = int(d["planning_unit_id"]) if d.get("planning_unit_id") else None
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.flush()

        # Sync assigned week time patterns
        assigned = d.get("assigned_pattern_ids", [])
        if assigned is not None:
            WorkTimePatternModelPattern.query.filter_by(work_time_pattern_model_id=item.id).delete()
            for pos, wtp_id in enumerate(assigned):
                link = WorkTimePatternModelPattern(
                    work_time_pattern_model_id=item.id,
                    week_time_pattern_id=int(wtp_id),
                    position=pos,
                )
                db.session.add(link)

        db.session.commit()
        return jsonify(success=True, item=item.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@scheduling_config_bp.route("/api/work-time-pattern-models/<int:item_id>", methods=["DELETE"])
@login_required
def delete_work_time_pattern_model(item_id):
    user, err = _admin_or_supervisor()
    if err:
        return err
    try:
        item = WorkTimePatternModel.query.get(item_id)
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# EMPLOYEE QUARTILES API
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/api/quartiles")
@login_required
def list_quartiles():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, items=_demo_quartiles())

    pu_id = request.args.get("planning_unit_id")
    q = EmployeeQuartile.query
    if pu_id:
        q = q.filter_by(planning_unit_id=int(pu_id))
    items = [eq.to_dict() for eq in q.order_by(EmployeeQuartile.quartile, EmployeeQuartile.employee_id).all()]
    return jsonify(success=True, items=items)


@scheduling_config_bp.route("/api/quartiles", methods=["POST"])
@login_required
def save_quartile():
    user, err = _admin_or_supervisor()
    if err:
        return err
    d = request.json or {}
    try:
        item = EmployeeQuartile.query.get(int(d["id"])) if d.get("id") else None
        if not item:
            # Check for existing by employee + planning unit
            item = EmployeeQuartile.query.filter_by(
                employee_id=int(d["employee_id"]),
                planning_unit_id=int(d["planning_unit_id"]) if d.get("planning_unit_id") else None,
            ).first()
        if not item:
            item = EmployeeQuartile()
            item.employee_id = int(d["employee_id"])
            item.planning_unit_id = int(d["planning_unit_id"]) if d.get("planning_unit_id") else None
            db.session.add(item)
        item.quartile = int(d.get("quartile", 4))
        if d.get("effective_date"):
            from datetime import datetime as _dt
            item.effective_date = _dt.strptime(d["effective_date"], "%Y-%m-%d").date()
        item.notes = d.get("notes", "")
        db.session.commit()
        return jsonify(success=True, item=item.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@scheduling_config_bp.route("/api/quartiles/bulk", methods=["POST"])
@login_required
def bulk_save_quartiles():
    """Bulk assign quartiles: {assignments: [{employee_id, quartile, planning_unit_id?}]}"""
    user, err = _admin_or_supervisor()
    if err:
        return err
    d = request.json or {}
    assignments = d.get("assignments", [])
    try:
        saved = 0
        for a in assignments:
            emp_id = int(a["employee_id"])
            pu_id = int(a["planning_unit_id"]) if a.get("planning_unit_id") else None
            item = EmployeeQuartile.query.filter_by(
                employee_id=emp_id, planning_unit_id=pu_id
            ).first()
            if not item:
                item = EmployeeQuartile(employee_id=emp_id, planning_unit_id=pu_id)
                db.session.add(item)
            item.quartile = int(a.get("quartile", 4))
            if a.get("effective_date"):
                from datetime import datetime as _dt
                item.effective_date = _dt.strptime(a["effective_date"], "%Y-%m-%d").date()
            saved += 1
        db.session.commit()
        return jsonify(success=True, saved=saved)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@scheduling_config_bp.route("/api/quartiles/<int:item_id>", methods=["DELETE"])
@login_required
def delete_quartile(item_id):
    user, err = _admin_or_supervisor()
    if err:
        return err
    try:
        item = EmployeeQuartile.query.get(item_id)
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SUPPORTING DATA API
# ═══════════════════════════════════════════════════════════════

@scheduling_config_bp.route("/api/supporting-data")
@login_required
def supporting_data():
    """Return planning units, shift templates, activities for the config forms."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True,
            planning_units=[{"id": 1, "name": "Self-Storage"}, {"id": 2, "name": "Portable Storage"}],
            shift_templates=[],
            activities=[],
            employees=[],
        )
    pus = [{"id": pu.id, "name": pu.name}
           for pu in PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()]
    sts = [{"id": st.id, "name": st.name, "start_time": st.start_time, "end_time": st.end_time}
           for st in ShiftTemplate.query.filter_by(is_active=True).order_by(ShiftTemplate.name).all()]
    acts = [a.to_dict() for a in Activity.query.filter_by(is_active=True).order_by(Activity.name).all()]
    emps = [{"id": e.id, "employee_id": e.employee_id, "name": e.full_name,
             "planning_unit_id": e.planning_unit_id}
            for e in Employee.query.filter_by(status="Active").order_by(Employee.last_name).all()]
    return jsonify(success=True, planning_units=pus, shift_templates=sts, activities=acts, employees=emps)


# ═══════════════════════════════════════════════════════════════
# DEMO DATA HELPERS
# ═══════════════════════════════════════════════════════════════

def _demo_day_models():
    return [
        {"id": 1, "name": "FT 8.5hrs 8:00-16:30", "abbreviation": "FT8.5-8", "start_time": "08:00",
         "end_time": "16:30", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#059669", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 0, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 2, "name": "FT 8.5hrs 8:30-17:00", "abbreviation": "FT8.5-830", "start_time": "08:30",
         "end_time": "17:00", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#2563eb", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 1, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 3, "name": "FT 8.5hrs 9:00-17:30", "abbreviation": "FT8.5-9", "start_time": "09:00",
         "end_time": "17:30", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#7c3aed", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 2, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 4, "name": "FT 8.5hrs 9:30-18:00", "abbreviation": "FT8.5-930", "start_time": "09:30",
         "end_time": "18:00", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#dc2626", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 3, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 5, "name": "FT 8.5hrs 10:00-18:30", "abbreviation": "FT8.5-10", "start_time": "10:00",
         "end_time": "18:30", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#ea580c", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 4, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 6, "name": "FT 10.5hrs 11:30-22:00", "abbreviation": "FT10.5", "start_time": "11:30",
         "end_time": "22:00", "paid_hours": 10.0, "total_hours": 10.5, "model_type": "Fixed",
         "color": "#be185d", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 5, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
        {"id": 7, "name": "FT 8.5hrs 13:30-22:00", "abbreviation": "FT8.5-1330", "start_time": "13:30",
         "end_time": "22:00", "paid_hours": 8.0, "total_hours": 8.5, "model_type": "Fixed",
         "color": "#4338ca", "day_type": "any", "planning_unit": "Self-Storage", "is_active": True,
         "activities": [], "sort_order": 6, "shift_template_id": None, "shift_template": "",
         "planning_unit_id": 1},
    ]


def _demo_week_time_patterns():
    return [
        {"id": 1, "name": "Self-Storage Q1", "abbreviation": "SS Q1", "days": {},
         "total_hours": 42.5, "max_exception_days": 0, "planning_unit": "Self-Storage",
         "planning_unit_id": 1, "is_active": True,
         "assigned_day_models": [
             {"id": 1, "day_model_id": 1, "day_model_name": "FT 8.5hrs 8:00-16:30", "start_time": "08:00", "end_time": "16:30", "color": "#059669", "position": 0},
             {"id": 2, "day_model_id": 2, "day_model_name": "FT 8.5hrs 8:30-17:00", "start_time": "08:30", "end_time": "17:00", "color": "#2563eb", "position": 1},
             {"id": 3, "day_model_id": 3, "day_model_name": "FT 8.5hrs 9:00-17:30", "start_time": "09:00", "end_time": "17:30", "color": "#7c3aed", "position": 2},
             {"id": 4, "day_model_id": 4, "day_model_name": "FT 8.5hrs 9:30-18:00", "start_time": "09:30", "end_time": "18:00", "color": "#dc2626", "position": 3},
         ]},
        {"id": 2, "name": "Self-Storage Q2", "abbreviation": "SS Q2", "days": {},
         "total_hours": 42.5, "max_exception_days": 0, "planning_unit": "Self-Storage",
         "planning_unit_id": 1, "is_active": True,
         "assigned_day_models": [
             {"id": 5, "day_model_id": 1, "day_model_name": "FT 8.5hrs 8:00-16:30", "start_time": "08:00", "end_time": "16:30", "color": "#059669", "position": 0},
             {"id": 6, "day_model_id": 2, "day_model_name": "FT 8.5hrs 8:30-17:00", "start_time": "08:30", "end_time": "17:00", "color": "#2563eb", "position": 1},
             {"id": 7, "day_model_id": 3, "day_model_name": "FT 8.5hrs 9:00-17:30", "start_time": "09:00", "end_time": "17:30", "color": "#7c3aed", "position": 2},
             {"id": 8, "day_model_id": 4, "day_model_name": "FT 8.5hrs 9:30-18:00", "start_time": "09:30", "end_time": "18:00", "color": "#dc2626", "position": 3},
             {"id": 9, "day_model_id": 5, "day_model_name": "FT 8.5hrs 10:00-18:30", "start_time": "10:00", "end_time": "18:30", "color": "#ea580c", "position": 4},
             {"id": 10, "day_model_id": 6, "day_model_name": "FT 10.5hrs 11:30-22:00", "start_time": "11:30", "end_time": "22:00", "color": "#be185d", "position": 5},
         ]},
        {"id": 3, "name": "Self-Storage Q3", "abbreviation": "SS Q3", "days": {},
         "total_hours": 42.5, "max_exception_days": 0, "planning_unit": "Self-Storage",
         "planning_unit_id": 1, "is_active": True,
         "assigned_day_models": [
             {"id": 11, "day_model_id": 1, "day_model_name": "FT 8.5hrs 8:00-16:30", "start_time": "08:00", "end_time": "16:30", "color": "#059669", "position": 0},
             {"id": 12, "day_model_id": 2, "day_model_name": "FT 8.5hrs 8:30-17:00", "start_time": "08:30", "end_time": "17:00", "color": "#2563eb", "position": 1},
             {"id": 13, "day_model_id": 3, "day_model_name": "FT 8.5hrs 9:00-17:30", "start_time": "09:00", "end_time": "17:30", "color": "#7c3aed", "position": 2},
             {"id": 14, "day_model_id": 4, "day_model_name": "FT 8.5hrs 9:30-18:00", "start_time": "09:30", "end_time": "18:00", "color": "#dc2626", "position": 3},
             {"id": 15, "day_model_id": 5, "day_model_name": "FT 8.5hrs 10:00-18:30", "start_time": "10:00", "end_time": "18:30", "color": "#ea580c", "position": 4},
             {"id": 16, "day_model_id": 6, "day_model_name": "FT 10.5hrs 11:30-22:00", "start_time": "11:30", "end_time": "22:00", "color": "#be185d", "position": 5},
             {"id": 17, "day_model_id": 7, "day_model_name": "FT 8.5hrs 13:30-22:00", "start_time": "13:30", "end_time": "22:00", "color": "#4338ca", "position": 6},
         ]},
    ]


def _demo_work_time_pattern_models():
    return [
        {"id": 1, "name": "Self-Storage Standard", "abbreviation": "SS", "model_type": "Fixed",
         "description": "Standard Self-Storage scheduling with Q1-Q4 shift eligibility",
         "patterns": [1, 2, 3], "exception_wtp_id": None, "exception_wtp_name": "",
         "planning_unit_id": 1, "planning_unit": "Self-Storage", "is_active": True,
         "assigned_patterns": [
             {"id": 1, "week_time_pattern_id": 1, "week_time_pattern_name": "Self-Storage Q1", "position": 0},
             {"id": 2, "week_time_pattern_id": 2, "week_time_pattern_name": "Self-Storage Q2", "position": 1},
             {"id": 3, "week_time_pattern_id": 3, "week_time_pattern_name": "Self-Storage Q3", "position": 2},
         ]},
    ]


def _demo_quartiles():
    from app.demo_data import DEMO_EMPLOYEES
    items = []
    for i, e in enumerate(DEMO_EMPLOYEES[:20]):
        q = (i % 4) + 1
        items.append({
            "id": i + 1,
            "employee_id": i + 1,
            "employee_name": f"{e.get('First Name', '')} {e.get('Last Name', '')}".strip(),
            "employee_ext_id": str(e.get("Employee ID", "")),
            "planning_unit_id": 1,
            "planning_unit": e.get("Latest Skill Name", "Self-Storage"),
            "quartile": q,
            "effective_date": "2024-01-01",
            "notes": "",
        })
    return items
