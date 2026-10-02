"""
routes/quality.py — Quality Management blueprint
=================================================
Quality evaluations, scoring analytics, and agent performance tracking.
"""

import logging
import datetime as _dt

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, admin_required, get_current_user
from app.models import db, QualityEvaluation, Employee, AnalyticsIntegration

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
                return jsonify({"success": False, "error": "Evaluation not found."}), 404
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
        return jsonify({"success": False, "error": "Evaluation not found."}), 404
    try:
        db.session.delete(ev)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════
# INTERACTION ANALYTICS INTEGRATION (Phase 9.6)
# ═════════════════════════════════════════════════════════════

import json as _json
import secrets as _secrets

# ── Integration management (admin) ───────────────────────────

@quality_bp.route("/api/integrations", methods=["POST"])
@login_required
@admin_required
def api_integrations_list():
    """List configured analytics integrations."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify({"integrations": _demo_integrations()})

    integrations = AnalyticsIntegration.query.order_by(
        AnalyticsIntegration.created_at.desc()
    ).all()
    return jsonify({"integrations": [i.to_dict() for i in integrations]})


@quality_bp.route("/api/integrations/save", methods=["POST"])
@login_required
@admin_required
def api_integration_save():
    """Create or update an analytics integration."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    platform = (data.get("platform") or "custom").strip().lower()

    if not name:
        return jsonify({"success": False, "error": "Name is required."})

    try:
        rec_id = data.get("id")
        if rec_id:
            integ = AnalyticsIntegration.query.get(rec_id)
            if not integ:
                return jsonify({"success": False, "error": "Integration not found."}), 404
        else:
            integ = AnalyticsIntegration(
                webhook_key=_secrets.token_urlsafe(24)
            )
            db.session.add(integ)

        integ.name = name
        integ.platform = platform
        integ.is_active = data.get("is_active", True)
        if data.get("field_map"):
            integ.field_map = _json.dumps(data["field_map"])

        db.session.commit()
        return jsonify({"success": True, "integration": integ.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@quality_bp.route("/api/integrations/delete", methods=["POST"])
@login_required
@admin_required
def api_integration_delete():
    """Delete an analytics integration."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")

    data = request.get_json(silent=True) or {}
    integ = AnalyticsIntegration.query.get(data.get("id"))
    if not integ:
        return jsonify({"success": False, "error": "Integration not found."}), 404
    try:
        db.session.delete(integ)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500


# ── Webhook receiver (no auth — uses webhook_key) ────────────

@quality_bp.route("/webhook/<webhook_key>", methods=["POST"])
def webhook_receive(webhook_key):
    """
    External QM platforms POST evaluation data here.
    No login required — authenticated by webhook_key.
    Accepts single evaluation or batch.

    Expected JSON:
    {
      "evaluations": [
        {
          "agent_id": "EMP123" or "employee_id": 45,
          "score": 85.5,
          "interaction_id": "CALL-2026-1234",
          "channel": "voice",
          "eval_date": "2026-10-01",
          "evaluator": "Auto-QM",
          "sub_scores": {"greeting": 90, "knowledge": 85, ...},
          "call_duration_secs": 320,
          "disposition": "resolved",
          "topics": ["billing", "upgrade"],
          "notes": "..."
        }
      ]
    }
    """
    integ = AnalyticsIntegration.query.filter_by(
        webhook_key=webhook_key, is_active=True
    ).first()
    if not integ:
        return jsonify({"success": False, "error": "Invalid or inactive webhook key."}), 401

    data = request.get_json(silent=True) or {}
    evaluations = data.get("evaluations") or data.get("data") or []
    if isinstance(evaluations, dict):
        evaluations = [evaluations]

    field_map = _json.loads(integ.field_map) if integ.field_map else {}
    created = 0
    errors = []

    for i, ev_data in enumerate(evaluations):
        try:
            # Resolve employee
            employee_id = ev_data.get(field_map.get("employee_id", "employee_id"))
            agent_ext_id = ev_data.get(field_map.get("agent_id", "agent_id"))

            employee = None
            if employee_id:
                employee = Employee.query.get(int(employee_id))
            if not employee and agent_ext_id:
                employee = Employee.query.filter(
                    (Employee.external_id_1 == str(agent_ext_id)) |
                    (Employee.external_id_2 == str(agent_ext_id))
                ).first()
            if not employee:
                errors.append({"index": i, "error": f"Agent not found: {agent_ext_id or employee_id}"})
                continue

            # Map fields
            score_field = field_map.get("score", "score")
            overall = float(ev_data.get(score_field, 0))
            sub = ev_data.get(field_map.get("sub_scores", "sub_scores")) or {}

            date_str = ev_data.get(field_map.get("eval_date", "eval_date"), "")
            eval_date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else _dt.date.today()

            topics = ev_data.get("topics") or []
            notes_parts = []
            if ev_data.get("notes"):
                notes_parts.append(ev_data["notes"])
            if topics:
                notes_parts.append(f"Topics: {', '.join(topics)}")
            notes_parts.append(f"Source: {integ.name} ({integ.platform})")

            qe = QualityEvaluation(
                employee_id=employee.id,
                evaluator=ev_data.get(field_map.get("evaluator", "evaluator"), integ.name),
                eval_date=eval_date,
                interaction_id=ev_data.get(field_map.get("interaction_id", "interaction_id"), ""),
                channel=ev_data.get(field_map.get("channel", "channel"), "voice"),
                lob=employee.lob or "",
                overall_score=overall,
                greeting_score=sub.get("greeting"),
                knowledge_score=sub.get("knowledge"),
                process_score=sub.get("process"),
                communication_score=sub.get("communication"),
                resolution_score=sub.get("resolution"),
                compliance_score=sub.get("compliance"),
                call_duration_secs=ev_data.get("call_duration_secs"),
                disposition=ev_data.get("disposition", ""),
                notes="\n".join(notes_parts),
                critical_fail=ev_data.get("critical_fail", False),
            )
            db.session.add(qe)
            created += 1
        except Exception as e:
            errors.append({"index": i, "error": str(e)})

    if created:
        try:
            integ.events_count = (integ.events_count or 0) + created
            integ.last_received = _dt.datetime.utcnow()
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": f"Failed to save evaluations: {e}"}), 500

    return jsonify({
        "success": True,
        "created": created,
        "errors": errors,
    })


# ── Agent performance merge (QM + adherence) ─────────────────

@quality_bp.route("/api/agent-performance", methods=["POST"])
@login_required
def api_agent_performance():
    """
    Merge quality scores with adherence data per agent.
    Returns combined view for the analytics integration dashboard.
    """
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    lob = data.get("lob", "All")
    date_from = data.get("date_from")
    date_to = data.get("date_to")

    if user and user.get("is_demo"):
        from app.demo_data import (get_demo_quality_evaluations,
                                    get_demo_adherence)
        evals = get_demo_quality_evaluations(lob=lob, date_from=date_from, date_to=date_to)
        from datetime import date
        adh = get_demo_adherence(lob=lob, date_obj=date.today())

        # Merge by agent
        agent_map = {}
        for ev in evals:
            name = ev.get("employee_name", "")
            if name not in agent_map:
                agent_map[name] = {
                    "employee_name": name,
                    "lob": ev.get("lob", ""),
                    "eval_count": 0,
                    "avg_score": 0,
                    "scores_sum": 0,
                    "critical_fails": 0,
                    "adherence_pct": None,
                    "channels": set(),
                }
            a = agent_map[name]
            a["eval_count"] += 1
            a["scores_sum"] += ev.get("overall_score", 0)
            if ev.get("critical_fail"):
                a["critical_fails"] += 1
            a["channels"].add(ev.get("channel", "voice"))

        for a in agent_map.values():
            a["avg_score"] = round(a["scores_sum"] / max(1, a["eval_count"]), 1)
            del a["scores_sum"]
            a["channels"] = sorted(a["channels"])

        # Add adherence data
        for rec in adh.get("adherence", []):
            name = rec.get("employee", "")
            if name in agent_map:
                agent_map[name]["adherence_pct"] = rec.get("adherence_pct")

        agents = sorted(agent_map.values(), key=lambda x: -x["avg_score"])
        return jsonify({"success": True, "agents": agents})

    # Real data path
    try:
        q = db.session.query(
            Employee.id,
            Employee.first_name,
            Employee.last_name,
            Employee.lob,
            db.func.count(QualityEvaluation.id).label("eval_count"),
            db.func.avg(QualityEvaluation.overall_score).label("avg_score"),
            db.func.sum(db.cast(QualityEvaluation.critical_fail, db.Integer)).label("crit_fails"),
        ).join(QualityEvaluation).group_by(
            Employee.id, Employee.first_name, Employee.last_name, Employee.lob
        )
        if lob and lob != "All":
            q = q.filter(QualityEvaluation.lob == lob)
        if date_from:
            q = q.filter(QualityEvaluation.eval_date >= date_from)
        if date_to:
            q = q.filter(QualityEvaluation.eval_date <= date_to)

        results = q.all()
        agents = []
        for r in results:
            agents.append({
                "employee_name": f"{r.first_name} {r.last_name}",
                "lob": r.lob or "",
                "eval_count": r.eval_count,
                "avg_score": round(float(r.avg_score or 0), 1),
                "critical_fails": r.crit_fails or 0,
                "adherence_pct": None,
            })
        return jsonify({"success": True, "agents": agents})
    except Exception as e:
        log.error(f"Agent performance error: {e}")
        return jsonify({"success": False, "error": str(e)})


def _demo_integrations():
    """Demo-mode fake integrations list."""
    return [
        {
            "id": 1, "name": "NICE CXone QM", "platform": "nice",
            "webhook_key": "demo-nice-key-xxxxx",
            "is_active": True, "field_map": {},
            "last_received": "2026-10-01T14:30:00",
            "events_count": 1247,
        },
        {
            "id": 2, "name": "Calabrio ONE", "platform": "calabrio",
            "webhook_key": "demo-calabrio-key-xxxxx",
            "is_active": True, "field_map": {},
            "last_received": "2026-10-01T16:45:00",
            "events_count": 892,
        },
        {
            "id": 3, "name": "Internal Scoring API", "platform": "custom",
            "webhook_key": "demo-custom-key-xxxxx",
            "is_active": False, "field_map": {},
            "last_received": None,
            "events_count": 0,
        },
    ]


def get_demo_adherence(lob="All"):
    """Simple demo adherence data for merging with QM scores."""
    import random
    random.seed(42)
    from app.demo_data import DEMO_EMPLOYEES
    results = []
    for emp in DEMO_EMPLOYEES:
        if lob != "All" and emp.get("lob") != lob:
            continue
        results.append({
            "employee": emp["name"],
            "adherence_pct": round(random.uniform(78, 100), 1),
        })
    return results
