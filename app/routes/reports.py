"""
Reports & Analytics — cross-module dashboard with key workforce metrics.
"""
import logging
from datetime import date, timedelta
from flask import Blueprint, jsonify, request, render_template
from sqlalchemy import func
from app.auth import login_required, get_current_user
from app.models import (
    db, Employee, Schedule, PTOEntry, ForecastInterval,
    QualityEvaluation, ShiftSwapRequest, ShiftBid, ShiftPost,
    PlanningUnit,
)

log = logging.getLogger(__name__)
reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _require_admin_or_sup():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return None
    return user


@reports_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        from flask import redirect
        return redirect("/")
    return render_template("reports/index.html")


@reports_bp.route("/api/summary", methods=["POST"])
@login_required
def summary():
    """Return summary metrics for the reports dashboard."""
    user = _require_admin_or_sup()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    try:
        payload = request.get_json(silent=True) or {}
        days = int(payload.get("days", 30))
        cutoff = date.today() - timedelta(days=days)

        # -- Headcount --
        total_employees = Employee.query.filter_by(status="Active").count()

        # -- Schedule stats --
        sched_q = Schedule.query.filter(Schedule.schedule_date >= cutoff)
        total_scheduled_hours = db.session.query(
            func.coalesce(func.sum(Schedule.hours), 0)
        ).filter(Schedule.schedule_date >= cutoff).scalar()
        total_shifts = sched_q.count()

        # -- PTO stats --
        pto_pending = PTOEntry.query.filter_by(approval_status="pending").count()
        pto_approved = PTOEntry.query.filter(
            PTOEntry.approval_status == "approved",
            PTOEntry.start_date >= cutoff,
        ).count()
        pto_denied = PTOEntry.query.filter(
            PTOEntry.approval_status == "denied",
            PTOEntry.start_date >= cutoff,
        ).count()

        # PTO days consumed
        pto_days = 0
        approved_entries = PTOEntry.query.filter(
            PTOEntry.approval_status == "approved",
            PTOEntry.start_date >= cutoff,
        ).all()
        for e in approved_entries:
            pto_days += (e.end_date - e.start_date).days + 1

        # -- Quality --
        quality_avg = db.session.query(
            func.avg(QualityEvaluation.overall_score)
        ).filter(QualityEvaluation.eval_date >= cutoff).scalar()
        quality_count = QualityEvaluation.query.filter(
            QualityEvaluation.eval_date >= cutoff
        ).count()

        # -- Shift bids & swaps --
        open_shifts = ShiftPost.query.filter_by(status="open").count()
        pending_bids = ShiftBid.query.filter_by(status="pending").count()
        pending_swaps = ShiftSwapRequest.query.filter(
            ShiftSwapRequest.status.in_(["pending", "accepted"])
        ).count()

        # -- Scheduling coverage by week --
        weekly_hours = []
        for w in range(min(days // 7, 12)):
            week_start = date.today() - timedelta(days=(w + 1) * 7)
            week_end = week_start + timedelta(days=6)
            hrs = db.session.query(
                func.coalesce(func.sum(Schedule.hours), 0)
            ).filter(
                Schedule.schedule_date >= week_start,
                Schedule.schedule_date <= week_end,
            ).scalar()
            weekly_hours.append({
                "week": week_start.isoformat(),
                "hours": round(float(hrs), 1),
            })
        weekly_hours.reverse()

        # -- PTO by type --
        pto_by_type = []
        try:
            from app.models import TimeOffType
            types = TimeOffType.query.all()
            for t in types:
                cnt = PTOEntry.query.filter(
                    PTOEntry.time_off_type_id == t.id,
                    PTOEntry.start_date >= cutoff,
                    PTOEntry.approval_status == "approved",
                ).count()
                if cnt:
                    pto_by_type.append({"type": t.label, "count": cnt})
        except Exception:
            pass

        # -- Quality trend by week --
        quality_trend = []
        for w in range(min(days // 7, 12)):
            week_start = date.today() - timedelta(days=(w + 1) * 7)
            week_end = week_start + timedelta(days=6)
            avg = db.session.query(
                func.avg(QualityEvaluation.overall_score)
            ).filter(
                QualityEvaluation.eval_date >= week_start,
                QualityEvaluation.eval_date <= week_end,
            ).scalar()
            quality_trend.append({
                "week": week_start.isoformat(),
                "avg_score": round(float(avg), 1) if avg else None,
            })
        quality_trend.reverse()

        return jsonify({
            "success": True,
            "period_days": days,
            "headcount": total_employees,
            "schedule": {
                "total_shifts": total_shifts,
                "total_hours": round(float(total_scheduled_hours), 1),
                "weekly_hours": weekly_hours,
            },
            "pto": {
                "pending": pto_pending,
                "approved": pto_approved,
                "denied": pto_denied,
                "days_consumed": pto_days,
                "by_type": pto_by_type,
            },
            "quality": {
                "avg_score": round(float(quality_avg), 1) if quality_avg else None,
                "eval_count": quality_count,
                "trend": quality_trend,
            },
            "self_service": {
                "open_shifts": open_shifts,
                "pending_bids": pending_bids,
                "pending_swaps": pending_swaps,
            },
        })
    except Exception as e:
        log.exception("Reports summary error")
        return jsonify({"success": False, "error": str(e)})
