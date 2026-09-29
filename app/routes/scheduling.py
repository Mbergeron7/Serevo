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

    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS
        lobs = list(DEMO_LOBS)
    else:
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

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import plan_demo_day, get_demo_requirements
            days = []
            d = start_date
            total_shifts = 0
            total_hours = 0.0
            cov_sum, cov_days = 0.0, 0
            never = {}
            while d <= end_date:
                all_scheds, warnings = plan_demo_day(d, lob)
                shifts_out = [s for s in all_scheds if s["status"] == "scheduled"]
                unassigned = [s["employee"] for s in all_scheds if s["status"] != "scheduled"]
                for s in all_scheds:
                    never.setdefault(s["employee"], True)
                    if s["status"] == "scheduled":
                        never[s["employee"]] = False
                reqs, _ = get_demo_requirements(lob, d)
                coverage = []
                for r in reqs:
                    t = r["time"][11:16]
                    tm = int(t[:2]) * 60 + int(t[3:])
                    sched = sum(1 for s in shifts_out
                                if (int(s["start"][:2]) * 60 + int(s["start"][3:])) <= tm <
                                   (int(s["end"][:2]) * 60 + int(s["end"][3:])))
                    req = r["agents_required"]
                    coverage.append({"time": t, "required": req, "scheduled": sched,
                                     "gap": sched - req,
                                     "coverage_pct": round(sched / req * 100, 1) if req else (100.0 if sched else 0)})
                understaffed = sum(1 for c in coverage if c["gap"] < 0)
                avg_pct = round(sum(c["coverage_pct"] for c in coverage) / len(coverage), 1) if coverage else 0
                summary = {"avg_coverage_pct": avg_pct,
                           "peak_required": max((c["required"] for c in coverage), default=0),
                           "peak_gap": min((c["gap"] for c in coverage), default=0),
                           "understaffed_intervals": understaffed, "total_intervals": len(coverage)}
                day_hours = sum(s.get("hours", 0) for s in shifts_out)
                total_shifts += len(shifts_out)
                total_hours += day_hours
                if coverage:
                    cov_sum += avg_pct; cov_days += 1
                days.append({
                    "date": d.isoformat(),
                    "date_label": d.strftime("%a %b %d"),
                    "day_of_week": d.strftime("%a"),
                    "shifts": shifts_out, "unassigned": unassigned,
                    "coverage": coverage, "summary": summary, "warnings": warnings,
                })
                d += datetime.timedelta(days=1)
            global_warnings = []
            never_names = sorted(n for n, v in never.items() if v)
            if never_names:
                global_warnings.append(
                    f"{len(never_names)} employee(s) were never scheduled across the entire range: "
                    + ", ".join(never_names) + ". Check their PTO, accommodations, and availability settings.")
            result = {
                "lob": lob, "start_date": start_str, "end_date": end_str,
                "days": days,
                "totals": {"total_shifts": total_shifts, "total_hours": round(total_hours, 1),
                           "total_days": len(days),
                           "avg_coverage_pct": round(cov_sum / cov_days, 1) if cov_days else 0},
                "warnings": global_warnings,
            }
        else:
            # mode: "overwrite" (default) — generate everything fresh; saved
            #       schedules are replaced when the user clicks Save.
            #       "fill"      — keep saved schedules in the range and only
            #       generate for employees/days that have none.
            mode = (payload.get("mode") or "overwrite").strip().lower()
            sheet = _get_sheet()
            emp_ids = payload.get("employee_ids") or None  # list or None

            existing_by_day = {}
            if mode == "fill":
                from app.models import PlanningUnit
                pu = PlanningUnit.query.filter(
                    db.func.lower(PlanningUnit.name) == lob.lower()
                ).first()
                if pu:
                    saved = Schedule.query.filter(
                        Schedule.planning_unit_id == pu.id,
                        Schedule.schedule_date >= start_date,
                        Schedule.schedule_date <= end_date,
                    ).all()
                    for s in saved:
                        existing_by_day.setdefault(s.schedule_date, []).append(s.to_dict())

            if not existing_by_day:
                result = generate_schedule_range(lob, start_date, end_date, shift_hrs, sheet, emp_ids)
            else:
                # Generate day by day, excluding employees who already have a saved shift
                from app.scheduling.engine import generate_shifts, analyze_coverage, coverage_summary, DAYS_OF_WEEK
                from app.people.manager import get_employees
                all_emps, _ = get_employees(sheet)
                lob_ids = [str(e.get("Employee ID", "")) for e in all_emps
                           if (e.get("Latest Skill Name") or "").strip().lower() == lob.lower()]
                if emp_ids:
                    lob_ids = [i for i in lob_ids if i in {str(x) for x in emp_ids}]

                days, total_shifts, total_hours, cov_sum, cov_days = [], 0, 0.0, 0.0, 0
                d = start_date
                while d <= end_date:
                    kept = existing_by_day.get(d, [])
                    kept_ids = {str(k["employee_id"]) for k in kept}
                    todo = [i for i in lob_ids if i not in kept_ids]
                    new_shifts, unassigned, warnings = ([], [], [])
                    if todo:
                        new_shifts, unassigned, warnings = generate_shifts(lob, d, shift_hrs, sheet, todo)
                    for k in kept:
                        k["saved"] = True
                    shifts = kept + new_shifts
                    shifts.sort(key=lambda s: (s["start"], s["employee"]))
                    coverage = analyze_coverage(lob, d, shifts, sheet)
                    summary = coverage_summary(coverage)
                    total_shifts += len(shifts)
                    total_hours += sum(float(s.get("hours") or 0) for s in shifts)
                    if summary["total_intervals"] > 0:
                        cov_sum += summary["avg_coverage_pct"]; cov_days += 1
                    if kept:
                        warnings.insert(0, f"Kept {len(kept)} saved shift(s)")
                    days.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "date_label": d.strftime("%a %b %d"),
                        "day_of_week": DAYS_OF_WEEK[d.weekday()],
                        "shifts": shifts, "unassigned": unassigned,
                        "coverage": coverage, "summary": summary, "warnings": warnings,
                    })
                    d += datetime.timedelta(days=1)
                result = {
                    "lob": lob, "start_date": start_str, "end_date": end_str, "days": days,
                    "totals": {"total_shifts": total_shifts, "total_hours": round(total_hours, 1),
                               "total_days": len(days),
                               "avg_coverage_pct": round(cov_sum / cov_days, 1) if cov_days else 0},
                    "warnings": [],
                }
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

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_requirements
            reqs, _ = get_demo_requirements(lob, date_obj)
            cov = []
            for r in reqs:
                # Count how many shifts cover this interval
                scheduled = 0
                for s in shifts:
                    if s.get("start") and s.get("end") and s["start"] <= r["time"] < s["end"]:
                        scheduled += 1
                cov.append({
                    "time": r["time"],
                    "required": r["agents_required"],
                    "scheduled": scheduled,
                    "delta": scheduled - r["agents_required"],
                })
            total_req = sum(c["required"] for c in cov)
            total_sched = sum(c["scheduled"] for c in cov)
            summary = {
                "avg_required": round(total_req / max(1, len(cov)), 1),
                "avg_scheduled": round(total_sched / max(1, len(cov)), 1),
                "coverage_pct": round(total_sched / max(1, total_req) * 100, 1),
            }
        else:
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

        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify({"success": True, "saved": len(shifts), "demo": True,
                            "message": "Saved (demo mode — changes are not persisted)"})

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

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_schedules, DEMO_EMPLOYEES
            lob_emp_ids = {e["Employee ID"] for e in DEMO_EMPLOYEES if e["Latest Skill Name"] == lob}
            shifts = []
            d = start_date
            while d <= end_date:
                day_scheds = get_demo_schedules(d)
                shifts.extend([s for s in day_scheds
                               if s["employee_id"] in lob_emp_ids and s.get("status") == "scheduled"])
                d += datetime.timedelta(days=1)
            return jsonify({"success": True, "shifts": shifts})

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
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "id": 0, "message": "Demo mode — change kept on screen only"})
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
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "id": 0, "message": "Demo mode — change kept on screen only"})
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
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "id": 0, "message": "Demo mode — change kept on screen only"})
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
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "id": 0, "message": "Demo mode — change kept on screen only"})
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
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "id": 0, "message": "Demo mode — change kept on screen only"})
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


# ── Clear saved schedules ────────────────────────────────────
@scheduling_bp.route("/clear", methods=["POST"])
@login_required
def clear_schedule():
    """
    Delete all saved schedules for a LOB + date range.
    POST JSON: {lob, start_date, end_date}
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

        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify({"success": True, "deleted": 0, "demo": True,
                            "message": "Schedules cleared (demo mode)."})

        from app.models import PlanningUnit
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob.lower()
        ).first()
        if not pu:
            return jsonify({"success": True, "deleted": 0})

        existing = Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date,
        ).all()
        count = len(existing)
        for s in existing:
            db.session.delete(s)
        db.session.commit()

        return jsonify({"success": True, "deleted": count})

    except Exception as e:
        log.exception("Clear schedule error")
        return jsonify({"success": False, "error": str(e)})


# ── Employees for LOB (API) ───────────────────────────────
@scheduling_bp.route("/employees", methods=["POST"])
@login_required
def employees_for_lob():
    """Return employees filtered by LOB for the employee picker."""
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import DEMO_EMPLOYEES
            emps = []
            for e in DEMO_EMPLOYEES:
                if lob and e.get("Latest Skill Name", "") != lob:
                    continue
                if str(e.get("Status", "")).strip().lower() in ("inactive", "terminated"):
                    continue
                first = str(e.get("First Name", "")).strip()
                last = str(e.get("Last Name", "")).strip()
                emps.append({
                    "employee_id": e.get("Employee ID", ""),
                    "name": f"{first} {last}".strip(),
                    "lob": e.get("Latest Skill Name", ""),
                })
            return jsonify({"success": True, "employees": emps})

        from app.scheduling.engine import _get_employees_for_lob
        sheet = _get_sheet()
        emps = _get_employees_for_lob(lob, sheet) if lob else []

        # If no LOB filter, return all active employees
        if not lob:
            from app.people.manager import get_active_employees
            active, _ = get_active_employees(sheet)
            emps = []
            for e in active:
                first = str(e.get("First Name", "")).strip()
                last = str(e.get("Last Name", "")).strip()
                emps.append({
                    "employee_id": e.get("Employee ID", ""),
                    "name": f"{first} {last}".strip(),
                    "lob": (e.get("Latest Skill Name") or "").strip(),
                })

        return jsonify({"success": True, "employees": emps})

    except Exception as e:
        log.exception("Employees for LOB error")
        return jsonify({"success": False, "error": str(e)})


# ── Import schedules from PeopleWare ────────────────────────
@scheduling_bp.route("/import/peopleware", methods=["POST"])
@login_required
def import_peopleware():
    """POST JSON: {lob?, start_date, end_date}
    Pulls schedules from the PeopleWare connection into Serevo for the range.
    If lob is given, only that LOB's employees are pulled."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify({"success": True, "created": 0, "replaced": 0, "skipped": 0,
                        "message": "Demo mode — schedules are pre-loaded"})
    try:
        payload = request.get_json(silent=True) or {}
        lob = (payload.get("lob") or "").strip()
        start_date = datetime.datetime.strptime(payload["start_date"], "%Y-%m-%d").date()
        end_date = datetime.datetime.strptime(payload["end_date"], "%Y-%m-%d").date()
        if end_date < start_date or (end_date - start_date).days > 62:
            return jsonify({"success": False, "error": "Choose a range of up to 62 days"})

        from app.capacity import planning as cp
        ids = None
        if lob and lob != "All":
            from app.scheduling.engine import _get_employees_for_lob
            ids = [e["employee_id"] for e in _get_employees_for_lob(lob, _get_sheet())]
            if not ids:
                return jsonify({"success": False, "error": f"No employees found for {lob} — pull the headcount first"})

        shifts = cp.fetch_pw_schedules(start_date, end_date, employee_ext_ids=ids)
        if not shifts:
            return jsonify({"success": False,
                            "error": "The connected system returned no schedules — check the API connection (Settings → API Connections) and that the date range has published schedules."})
        created, replaced, skipped = cp.upsert_pw_schedules(shifts)
        msg = f"Imported {created} shift(s) from the connected system"
        if replaced:
            msg += f", replaced {replaced} existing"
        if skipped:
            msg += f", skipped {skipped} for employees not in Serevo (pull headcount first)"
        return jsonify({"success": True, "created": created, "replaced": replaced,
                        "skipped": skipped, "message": msg})
    except Exception as e:
        log.exception("PeopleWare schedule import error")
        return jsonify({"success": False, "error": str(e)})


# ── Helpers for manual shift entry ──────────────────────────
@scheduling_bp.route("/segments/auto", methods=["POST"])
@login_required
def segments_auto():
    """POST {start, end, type, existing_count?} → {segments:[...]} using the
    segment-code rules (break/lunch placement), staggered by existing_count."""
    try:
        payload = request.get_json(silent=True) or {}
        from app.scheduling.engine import _generate_segments
        idx = int(payload.get("existing_count") or 0)
        segs = _generate_segments(payload.get("start", "09:00"), payload.get("end", "17:00"),
                                  payload.get("type", "full"), stagger_index=idx,
                                  total_employees=idx + 1)
        for s_ in segs:
            s_.setdefault("notes", "")
        return jsonify({"success": True, "segments": segs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "segments": []})


@scheduling_bp.route("/shift-templates", methods=["POST"])
@login_required
def shift_templates():
    """Active shift templates for the Add Shift picker."""
    user = get_current_user()
    try:
        if user and user.get("is_demo"):
            from app.demo_data import get_demo_settings_data
            t = [{"name": x["name"], "start_time": x["start_time"], "end_time": x["end_time"],
                  "shift_type": x.get("shift_type", "full")} for x in get_demo_settings_data()["shifts"]]
            return jsonify({"success": True, "templates": t})
        from app.models import ShiftTemplate
        rows = ShiftTemplate.query.filter_by(is_active=True).order_by(ShiftTemplate.sort_order, ShiftTemplate.name).all()
        return jsonify({"success": True, "templates": [
            {"name": r.name, "start_time": r.start_time, "end_time": r.end_time, "shift_type": r.shift_type or "full"}
            for r in rows]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "templates": []})
