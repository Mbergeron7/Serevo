"""
routes/quality.py — Quality Management blueprint
=================================================
Quality evaluations, scoring analytics, and agent performance tracking.
"""

import logging
import datetime as _dt

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, admin_required, get_current_user
from app.models import db, QualityEvaluation, Employee

log = logging.getLogger("serevo.quality")

quality_bp = Blueprint("quality", __name__, url_prefix="/quality",
                       template_folder="../templates/quality")


@quality_bp.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("quality/index.html", user=user)


# ── API: list evaluations ──────────────────────────────────────
@quality_bp.route("/api/evaluations", methods=["POST"])
@login_required
def api_evaluations():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    lob = data.get("lob", "All")
    date_from = data.get("date_from")
    date_to = data.get("date_to")

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_quality_evaluations
        evals = get_demo_quality_evaluations(lob=lob, date_from=date_from, date_to=date_to)
        return jsonify({"evaluations": evals})

    try:
        q = QualityEvaluation.query
        if lob and lob != "All":
            q = q.filter(QualityEvaluation.lob == lob)
        if date_from:
            q = q.filter(QualityEvaluation.eval_date >= _dt.date.fromisoformat(date_from))
        if date_to:
            q = q.filter(QualityEvaluation.eval_date <= _dt.date.fromisoformat(date_to))
        evals = q.order_by(QualityEvaluation.eval_date.desc()).limit(200).all()
        return jsonify({"evaluations": [e.to_dict() for e in evals]})
    except Exception as e:
        db.session.rollback()
        return jsonify({"evaluations": [], "error": str(e)})


# ── API: dashboard stats ───────────────────────────────────────
@quality_bp.route("/api/dashboard", methods=["POST"])
@login_required
def api_dashboard():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    lob = data.get("lob", "All")

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_quality_dashboard
        return jsonify(get_demo_quality_dashboard(lob))

    try:
        q = QualityEvaluation.query
        if lob and lob != "All":
            q = q.filter(QualityEvaluation.lob == lob)

        evals = q.order_by(QualityEvaluation.eval_date.desc()).limit(500).all()
        if not evals:
            return jsonify({"avg_score": 0, "total_evals": 0, "critical_fails": 0,
                           "by_agent": [], "by_category": {}, "trend": []})

        total = len(evals)
        avg = sum(e.overall_score for e in evals) / total
        crit = sum(1 for e in evals if e.critical_fail)

        # By agent
        agent_scores = {}
        for e in evals:
            name = f"{e.employee.first_name} {e.employee.last_name}" if e.employee else "Unknown"
            agent_scores.setdefault(name, []).append(e.overall_score)
        by_agent = sorted([
            {"name": k, "avg_score": round(sum(v)/len(v), 1), "eval_count": len(v)}
            for k, v in agent_scores.items()
        ], key=lambda x: -x["avg_score"])

        # Sub-score averages
        cats = {}
        for attr in ["greeting_score", "knowledge_score", "process_score",
                      "communication_score", "resolution_score", "compliance_score"]:
            vals = [getattr(e, attr) for e in evals if getattr(e, attr) is not None]
            cats[attr.replace("_score", "")] = round(sum(vals)/len(vals), 1) if vals else None

        return jsonify({
            "avg_score": round(avg, 1),
            "total_evals": total,
            "critical_fails": crit,
            "by_agent": by_agent[:20],
            "by_category": cats,
            "trend": [],
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)})


# ── API: save evaluation ───────────────────────────────────────
@quality_bp.route("/api/evaluations/save", methods=["POST"])
@login_required
@admin_required
def api_eval_save():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")

    data = request.get_json(silent=True) or {}
    eval_id = data.get("id")

    try:
        if eval_id:
            ev = QualityEvaluation.query.get(eval_id)
            if not ev:
                return jsonify({"success": False, "error": "Evaluation not found."})
        else:
            ev = QualityEvaluation(employee_id=data["employee_id"],
                                   overall_score=0)
            db.session.add(ev)

        ev.evaluator = data.get("evaluator", "")
        ev.eval_date = _dt.date.fromisoformat(data["eval_date"])
        ev.interaction_id = data.get("interaction_id") or None
        ev.channel = data.get("channel", "voice")
        ev.lob = data.get("lob", "")
        ev.overall_score = float(data.get("overall_score", 0))
        ev.greeting_score = float(data["greeting_score"]) if data.get("greeting_score") else None
        ev.knowledge_score = float(data["knowledge_score"]) if data.get("knowledge_score") else None
        ev.process_score = float(data["process_score"]) if data.get("process_score") else None
        ev.communication_score = float(data["communication_score"]) if data.get("communication_score") else None
        ev.resolution_score = float(data["resolution_score"]) if data.get("resolution_score") else None
        ev.compliance_score = float(data["compliance_score"]) if data.get("compliance_score") else None
        ev.call_duration_secs = int(data["call_duration_secs"]) if data.get("call_duration_secs") else None
        ev.disposition = data.get("disposition", "")
        ev.notes = data.get("notes", "")
        ev.critical_fail = bool(data.get("critical_fail"))

        db.session.commit()
        return jsonify({"success": True, "evaluation": ev.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


# ── API: delete evaluation ─────────────────────────────────────
@quality_bp.route("/api/evaluations/delete", methods=["POST"])
@login_required
@admin_required
def api_eval_delete():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")

    data = request.get_json(silent=True) or {}
    ev = QualityEvaluation.query.get(data.get("id"))
    if not ev:
        return jsonify({"success": False, "error": "Evaluation not found."})
    db.session.delete(ev)
    db.session.commit()
    return jsonify({"success": True})
