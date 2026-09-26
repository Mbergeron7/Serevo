"""
routes/scheduling.py — Scheduling blueprint
=============================================
Schedule generation, shift assignments, coverage analysis,
and full CRUD for persistent schedules with shift segments.
"""

import io
import csv
import logging
import datetime

from flask import (Blueprint, render_template, request, jsonify,
                   Response)
from app.auth import login_required, get_current_user
from app.models import db, Schedule, ShiftSegment, Employee

log = logging.getLogger("serevo.scheduling")

scheduling_bp = Blueprint("scheduling", __name__, url_prefix="/scheduling")


def _get_sheet():
    """Return the capacity Google Sheet object, or None."""
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        if err:
            return None
        return sheet
    except Exception:
        return None


def _parse_time(t):
    """Parse 'HH:MM' string to a datetime.time object."""
    if not t:
        return None
    parts = t.strip().split(":")
    return datetime.time(int(parts[0]), int(parts[1]))


def _time_diff_hours(start, end):
    """Calculate hours between two time objects."""
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    return round((e - s) / 60, 1)


# ── Main view ───────────────────────────────────────────────
@scheduling_bp.route("/")
@login_required
def index():
    user = get_current_user()
    from app.scheduling.engine import get_available_lobs

    sheet = _get_sheet()
    lobs = get_available_lobs(sheet)

    return render_template("scheduling/index.html",
        user=user,
        lobs=lobs,
    )


# ── Generate schedule (API) ─────────────────────────────────
@scheduling_bp.route("/generate", methods=["POST"])
@login_required
def generate():
    """
    Generate shifts + coverage for a LOB and date range.
    POST JSON: {lob, start_date, end_date, shift_length_hrs?}
    Returns JSON with days[], each containing shifts, coverage, summary.
    """
    from app.scheduling.engine import generate_schedule_range

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")
        shift_hrs = float(payload.get("shift_length_hrs", 8))

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})
        if not start_str or not end_str:
            return jsonify({"success": False, "error": "Start and end dates are required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()

        if end_date < start_date:
            return jsonify({"success": False, "error": "End date must be after start date"})
        if (end_date - start_date).days > 366:
            return jsonify({"success": False, "error": "Date range cannot exceed one year"})

        sheet = _get_sheet()
        result = generate_schedule_range(lob, start_date, end_date, shift_hrs, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Schedule generation error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Coverage data (API) ────────────────────────────────────
@scheduling_bp.route("/coverage", methods=["POST"])
@login_required
def coverage():
    """
    Get coverage analysis for a single date.
    POST JSON: {lob, date, shifts: [{start, end}, ...]}
    """
    from app.scheduling.engine import analyze_coverage, coverage_summary

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")
        shifts = payload.get("shifts", [])

        if not lob or not date_str:
            return jsonify({"success": False, "error": "LOB and date are required"})

        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        sheet = _get_sheet()
        cov = analyze_coverage(lob, date_obj, shifts, sheet)
        summary = coverage_summary(cov)

        return jsonify({
            "success": True,
            "coverage": cov,
            "summary": summary,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ═════════════════════════════════════════════════════════════
# SCHEDULE CRUD — Persistent DB operations
# ═════════════════════════════════════════════════════════════

@scheduling_bp.route("/save", methods=["POST"])
@login_required
def save_schedule():
    """
    Save a generated schedule to the database.
    POST JSON: {lob, date, shifts: [{employee_id, start, end, hours, type, segments}]}
    Replaces any existing schedule for that LOB+date.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")
        shifts = payload.get("shifts", [])

        if not lob or not date_str:
            return jsonify({"success": False, "error": "LOB and date required"})

        sched_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        # Find or create planning unit
        from app.models import PlanningUnit
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob.lower()
        ).first()
        pu_id = pu.id if pu else None

        # Delete existing schedules for this LOB+date
        existing = Schedule.query.filter_by(
            schedule_date=sched_date, planning_unit_id=pu_id
        ).all()
        for s in existing:
            db.session.delete(s)

        saved = 0
        for shift in shifts:
            emp_ext_id = str(shift.get("employee_id", "")).strip()
            if not emp_ext_id:
                continue

            emp = Employee.query.filter_by(employee_id=emp_ext_id).first()
            if not emp:
                continue

            start_t = _parse_time(shift.get("start", "08:00"))
            end_t = _parse_time(shift.get("end", "16:00"))
            hours = float(shift.get("hours", 0))
            if not hours and start_t and end_t:
                hours = _time_diff_hours(start_t, end_t)

            sched = Schedule(
                employee_id=emp.id,
                planning_unit_id=pu_id,
                schedule_date=sched_date,
                shift_start=start_t,
                shift_end=end_t,
                shift_type=shift.get("type", "full"),
                hours=hours,
                status="scheduled",
            )
            db.session.add(sched)
            db.session.flush()  # get sched.id

            # Save segments
            for idx, seg in enumerate(shift.get("segments", [])):
                segment = ShiftSegment(
                    schedule_id=sched.id,
                    activity_type=seg.get("type", "on-call"),
                    start_time=_parse_time(seg.get("start", "")),
                    end_time=_parse_time(seg.get("end", "")),
                    duration_mins=int(seg.get("duration_mins", 0)),
                    sort_order=idx,
                    notes=seg.get("notes", ""),
                )
                db.session.add(segment)
            saved += 1

        db.session.commit()
        return jsonify({"success": True, "saved": saved})

    except Exception as e:
        db.session.rollback()
        log.exception("Schedule save error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/load", methods=["POST"])
@login_required
def load_schedule():
    """
    Load saved schedule from DB.
    POST JSON: {lob, start_date, end_date}
    Returns {shifts: [{id, employee, date, start, end, ...}]}
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")

        if not lob or not start_str or not end_str:
            return jsonify({"success": False, "error": "LOB and date range required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()

        from app.models import PlanningUnit
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob.lower()
        ).first()
        if not pu:
            return jsonify({"success": True, "shifts": [], "message": "No planning unit found"})

        schedules = Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date,
        ).order_by(Schedule.schedule_date, Schedule.shift_start).all()

        shifts = [s.to_dict() for s in schedules]
        return jsonify({"success": True, "shifts": shifts})

    except Exception as e:
        log.exception("Schedule load error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/shift/<int:shift_id>", methods=["PUT"])
@login_required
def update_shift(shift_id):
    """
    Update a single shift's times, type, or status.
    PUT JSON: {start?, end?, type?, status?}
    """
    try:
        sched = Schedule.query.get(shift_id)
        if not sched:
            return jsonify({"success": False, "error": "Shift not found"})

        payload = request.get_json(silent=True) or {}

        if "start" in payload:
            sched.shift_start = _parse_time(payload["start"])
        if "end" in payload:
            sched.shift_end = _parse_time(payload["end"])
        if "type" in payload:
            sched.shift_type = payload["type"]
        if "status" in payload:
            sched.status = payload["status"]

        # Recalculate hours
        if sched.shift_start and sched.shift_end:
            sched.hours = _time_diff_hours(sched.shift_start, sched.shift_end)

        db.session.commit()
        return jsonify({"success": True, "shift": sched.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/shift/<int:shift_id>", methods=["DELETE"])
@login_required
def delete_shift(shift_id):
    """Delete a shift and its segments."""
    try:
        sched = Schedule.query.get(shift_id)
        if not sched:
            return jsonify({"success": False, "error": "Shift not found"})

        db.session.delete(sched)
        db.session.commit()
        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/shift/<int:shift_id>/segment", methods=["POST"])
@login_required
def add_segment(shift_id):
    """
    Add a segment to a shift.
    POST JSON: {type, start, end, notes?}
    """
    try:
        sched = Schedule.query.get(shift_id)
        if not sched:
            return jsonify({"success": False, "error": "Shift not found"})

        payload = request.get_json(silent=True) or {}
        start_t = _parse_time(payload.get("start", ""))
        end_t = _parse_time(payload.get("end", ""))
        if not start_t or not end_t:
            return jsonify({"success": False, "error": "Start and end times required"})

        dur = (end_t.hour * 60 + end_t.minute) - (start_t.hour * 60 + start_t.minute)

        # Get next sort order
        max_order = db.session.query(db.func.max(ShiftSegment.sort_order)).filter_by(
            schedule_id=shift_id).scalar() or 0

        seg = ShiftSegment(
            schedule_id=shift_id,
            activity_type=payload.get("type", "on-call"),
            start_time=start_t,
            end_time=end_t,
            duration_mins=dur,
            sort_order=max_order + 1,
            notes=payload.get("notes", ""),
        )
        db.session.add(seg)
        db.session.commit()
        return jsonify({"success": True, "segment": seg.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/segment/<int:seg_id>", methods=["PUT"])
@login_required
def update_segment(seg_id):
    """
    Update a segment.
    PUT JSON: {type?, start?, end?, notes?}
    """
    try:
        seg = ShiftSegment.query.get(seg_id)
        if not seg:
            return jsonify({"success": False, "error": "Segment not found"})

        payload = request.get_json(silent=True) or {}
        if "type" in payload:
            seg.activity_type = payload["type"]
        if "start" in payload:
            seg.start_time = _parse_time(payload["start"])
        if "end" in payload:
            seg.end_time = _parse_time(payload["end"])
        if "notes" in payload:
            seg.notes = payload["notes"]

        if seg.start_time and seg.end_time:
            seg.duration_mins = (seg.end_time.hour * 60 + seg.end_time.minute) - \
                                (seg.start_time.hour * 60 + seg.start_time.minute)

        db.session.commit()
        return jsonify({"success": True, "segment": seg.to_dict()})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/segment/<int:seg_id>", methods=["DELETE"])
@login_required
def delete_segment(seg_id):
    """Delete a segment."""
    try:
        seg = ShiftSegment.query.get(seg_id)
        if not seg:
            return jsonify({"success": False, "error": "Segment not found"})

        db.session.delete(seg)
        db.session.commit()
        return jsonify({"success": True})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


# ═════════════════════════════════════════════════════════════
# EXPORT
# ═════════════════════════════════════════════════════════════

@scheduling_bp.route("/export", methods=["POST"])
@login_required
def export_csv():
    """
    Export schedule to CSV.
    POST JSON: {lob, start_date, end_date}
    Returns CSV file download.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        start_str = payload.get("start_date", "")
        end_str = payload.get("end_date", "")

        if not lob or not start_str or not end_str:
            return jsonify({"success": False, "error": "LOB and date range required"})

        start_date = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()

        from app.models import PlanningUnit
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob.lower()
        ).first()
        if not pu:
            return jsonify({"success": False, "error": "No planning unit found"})

        schedules = Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date,
        ).order_by(Schedule.schedule_date, Schedule.shift_start).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Date", "Employee", "Employee ID", "Shift Start", "Shift End",
            "Hours", "Type", "Status", "Segment Type", "Seg Start",
            "Seg End", "Seg Duration", "Seg Notes"
        ])

        for s in schedules:
            d = s.to_dict()
            if d["segments"]:
                for seg in d["segments"]:
                    writer.writerow([
                        d["date"], d["employee"], d["employee_id"],
                        d["start"], d["end"], d["hours"], d["type"],
                        d["status"], seg["type"], seg["start"],
                        seg["end"], seg["duration_mins"], seg["notes"],
                    ])
            else:
                writer.writerow([
                    d["date"], d["employee"], d["employee_id"],
                    d["start"], d["end"], d["hours"], d["type"],
                    d["status"], "", "", "", "", "",
                ])

        output.seek(0)
        filename = f"schedule_{lob}_{start_str}_to_{end_str}.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except Exception as e:
        log.exception("Export error")
        return jsonify({"success": False, "error": str(e)})
