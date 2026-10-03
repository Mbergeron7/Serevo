"""
Training — manage training modules and employee assignments.
"""
import logging
from datetime import date, datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import db, TrainingModule, TrainingAssignment, Employee, User

log = logging.getLogger(__name__)
training_bp = Blueprint("training", __name__, url_prefix="/training")


def _utcnow():
    return datetime.now(timezone.utc)


def _check_role(user):
    return user and user.get("role") in ("admin", "supervisor")


@training_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not _check_role(user):
        return "Forbidden", 403
    employees = Employee.query.filter(Employee.status == "Active").order_by(Employee.last_name).all()
    return render_template("training/index.html", user=user, employees=employees)


# ── Modules CRUD ──────────────────────────────────────────────

@training_bp.route("/api/modules", methods=["POST"])
@login_required
def list_modules():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    modules = TrainingModule.query.filter(TrainingModule.is_active == True).order_by(TrainingModule.title).all()
    rows = []
    for m in modules:
        assigned = TrainingAssignment.query.filter_by(module_id=m.id).count()
        completed = TrainingAssignment.query.filter_by(module_id=m.id, status="completed").count()
        rows.append({
            "id": m.id,
            "title": m.title,
            "description": m.description or "",
            "category": m.category,
            "duration_mins": m.duration_mins,
            "is_required": m.is_required,
            "assigned_count": assigned,
            "completed_count": completed,
        })
    return jsonify(success=True, modules=rows)


@training_bp.route("/api/modules/save", methods=["POST"])
@login_required
def save_module():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify(success=False, error="Title is required")

    mod_id = data.get("id")
    if mod_id:
        mod = TrainingModule.query.get(mod_id)
        if not mod:
            return jsonify(success=False, error="Not found")
    else:
        mod = TrainingModule(created_by=user["id"])
        db.session.add(mod)

    mod.title = title
    mod.description = (data.get("description") or "").strip()
    mod.category = data.get("category", "general")
    mod.duration_mins = int(data.get("duration_mins", 60) or 60)
    mod.is_required = bool(data.get("is_required", False))

    db.session.commit()
    return jsonify(success=True, id=mod.id)


@training_bp.route("/api/modules/delete", methods=["POST"])
@login_required
def delete_module():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    mod = TrainingModule.query.get(data.get("id"))
    if not mod:
        return jsonify(success=False, error="Not found")

    mod.is_active = False
    db.session.commit()
    return jsonify(success=True)


# ── Assignments ───────────────────────────────────────────────

@training_bp.route("/api/assignments", methods=["POST"])
@login_required
def list_assignments():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    module_id = data.get("module_id")
    employee_id = data.get("employee_id")
    status_filter = data.get("status", "")

    q = TrainingAssignment.query.join(TrainingModule).filter(TrainingModule.is_active == True)
    if module_id:
        q = q.filter(TrainingAssignment.module_id == module_id)
    if employee_id:
        q = q.filter(TrainingAssignment.employee_id == employee_id)
    if status_filter:
        q = q.filter(TrainingAssignment.status == status_filter)
    q = q.order_by(TrainingAssignment.assigned_at.desc()).limit(300)

    rows = []
    for a in q.all():
        emp = Employee.query.get(a.employee_id)
        rows.append({
            "id": a.id,
            "module_id": a.module_id,
            "module_title": a.module.title if a.module else "",
            "module_category": a.module.category if a.module else "",
            "employee_id": a.employee_id,
            "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "Unknown",
            "status": a.status,
            "due_date": a.due_date.isoformat() if a.due_date else "",
            "completed_at": a.completed_at.isoformat() if a.completed_at else "",
            "score": a.score,
            "notes": a.notes or "",
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else "",
        })
    return jsonify(success=True, assignments=rows)


@training_bp.route("/api/assignments/assign", methods=["POST"])
@login_required
def assign_training():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    module_id = data.get("module_id")
    employee_ids = data.get("employee_ids", [])
    due_date_str = data.get("due_date", "")

    if not module_id or not employee_ids:
        return jsonify(success=False, error="Module and at least one employee required")

    mod = TrainingModule.query.get(module_id)
    if not mod:
        return jsonify(success=False, error="Module not found")

    due_date = None
    if due_date_str:
        try:
            due_date = date.fromisoformat(due_date_str)
        except ValueError:
            pass

    created = 0
    for eid in employee_ids:
        existing = TrainingAssignment.query.filter_by(module_id=module_id, employee_id=eid).first()
        if existing:
            continue
        a = TrainingAssignment(
            module_id=module_id,
            employee_id=eid,
            due_date=due_date,
            assigned_by=user["id"],
        )
        db.session.add(a)
        created += 1

    db.session.commit()
    return jsonify(success=True, created=created)


@training_bp.route("/api/assignments/update", methods=["POST"])
@login_required
def update_assignment():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    a = TrainingAssignment.query.get(data.get("id"))
    if not a:
        return jsonify(success=False, error="Not found")

    if "status" in data:
        a.status = data["status"]
        if data["status"] == "completed" and not a.completed_at:
            a.completed_at = _utcnow()
    if "score" in data and data["score"] is not None:
        a.score = float(data["score"])
    if "notes" in data:
        a.notes = (data["notes"] or "").strip()
    if "due_date" in data:
        try:
            a.due_date = date.fromisoformat(data["due_date"]) if data["due_date"] else None
        except ValueError:
            pass

    db.session.commit()
    return jsonify(success=True)


@training_bp.route("/api/assignments/delete", methods=["POST"])
@login_required
def delete_assignment():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    a = TrainingAssignment.query.get(data.get("id"))
    if not a:
        return jsonify(success=False, error="Not found")

    db.session.delete(a)
    db.session.commit()
    return jsonify(success=True)
