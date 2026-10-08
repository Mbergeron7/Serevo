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
import threading
import uuid

from collections import defaultdict
from flask import (Blueprint, render_template, request, jsonify,
                   Response)
from app.auth import login_required, admin_required, get_current_user
from app.models import db, Schedule, ShiftSegment, Employee

log = logging.getLogger("serevo.scheduling")

scheduling_bp = Blueprint("scheduling", __name__, url_prefix="/scheduling")

# ── In-memory job store for async schedule generation ──────
_schedule_jobs = {}  # job_id -> {status, result, error, started_at}
_jobs_lock = threading.Lock()



from app.routes._utils import get_sheet as _get_sheet


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
        from app.demo_data import DEMO_LOBS, get_demo_selections, get_demo_skills_config, get_demo_shift_sequences, get_demo_customization
        lobs = list(DEMO_LOBS)
        selections = get_demo_selections()
        skills = get_demo_skills_config()
        shift_sequences = get_demo_shift_sequences().get("items", [])
        segment_codes = get_demo_customization().get("segments", [])
    else:
        from app.scheduling.engine import get_available_lobs
        from app.models import Selection, SkillGroup, ShiftSequence, SegmentCode
        sheet = _get_sheet()
        lobs = get_available_lobs(sheet)
        try:
            selections = [{"id": s.id, "name": s.name} for s in Selection.query.filter_by(is_active=True).order_by(Selection.name).all()]
        except Exception:
            log.warning("Failed to load selections", exc_info=True)
            selections = []
        try:
            skills = [{"id": s.id, "name": s.name} for s in SkillGroup.query.filter_by(is_active=True).order_by(SkillGroup.name).all()]
        except Exception:
            log.warning("Failed to load skills", exc_info=True)
            skills = []
        try:
            shift_sequences = [{"id": s.id, "name": s.name} for s in ShiftSequence.query.filter_by(is_active=True).order_by(ShiftSequence.name).all()]
        except Exception:
            log.warning("Failed to load shift sequences", exc_info=True)
            shift_sequences = []
        try:
            segment_codes = [s.to_dict() for s in SegmentCode.query.filter_by(is_active=True).order_by(SegmentCode.sort_order, SegmentCode.label).all()]
        except Exception:
            log.warning("Failed to load segment codes", exc_info=True)
            segment_codes = []

    return render_template("scheduling/index.html",
        user=user,
        lobs=lobs,
        selections=selections,
        skills=skills,
        shift_sequences=shift_sequences,
        segment_codes=segment_codes,
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
        emp_ids = payload.get("employee_ids") or None  # list or None

        # Demo mode runs synchronously (fast, no DB)
        if user and user.get("is_demo"):
            result = _generate_demo_schedule(lob, start_date, end_date, emp_ids)
            result["success"] = True
            return jsonify(result)

        # Live mode: run in background thread to avoid Render worker timeout
        mode = (payload.get("mode") or "overwrite").strip().lower()
        job_id = str(uuid.uuid4())[:12]

        with _jobs_lock:
            # Clean up old jobs (keep last 20)
            if len(_schedule_jobs) > 20:
                oldest = sorted(_schedule_jobs.keys(),
                                key=lambda k: _schedule_jobs[k].get("started_at", ""))
                for old_key in oldest[:len(oldest) - 10]:
                    _schedule_jobs.pop(old_key, None)
            _schedule_jobs[job_id] = {
                "status": "running",
                "result": None,
                "error": None,
                "started_at": datetime.datetime.utcnow().isoformat(),
            }

        from flask import current_app
        app = current_app._get_current_object()

        def _bg_generate():
            with app.app_context():
                try:
                    result = _do_generate(lob, start_date, end_date,
                                          shift_hrs, emp_ids, mode)
                    result["success"] = True
                    with _jobs_lock:
                        _schedule_jobs[job_id]["status"] = "done"
                        _schedule_jobs[job_id]["result"] = result
                except Exception as e:
                    log.exception("Background schedule generation failed")
                    with _jobs_lock:
                        _schedule_jobs[job_id]["status"] = "error"
                        _schedule_jobs[job_id]["error"] = str(e)

        t = threading.Thread(target=_bg_generate, daemon=True)
        t.start()

        return jsonify({"success": True, "poll": True, "job_id": job_id,
                        "message": "Schedule generation started…"})

    except Exception as e:
        log.error(f"Schedule generation error: {e}")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/generate/status", methods=["GET"])
@login_required
def generate_status():
    """Poll for async schedule generation results."""
    job_id = request.args.get("job_id", "")
    if not job_id:
        return jsonify({"success": False, "error": "Missing job_id"})

    with _jobs_lock:
        job = _schedule_jobs.get(job_id)

    if not job:
        return jsonify({"success": False, "error": "Job not found"})

    if job["status"] == "running":
        return jsonify({"success": True, "status": "running"})
    elif job["status"] == "error":
        # Clean up
        with _jobs_lock:
            _schedule_jobs.pop(job_id, None)
        return jsonify({"success": False, "error": job["error"]})
    else:
        # Done — return the full result and clean up
        result = job["result"]
        with _jobs_lock:
            _schedule_jobs.pop(job_id, None)
        return jsonify(result)


def _generate_demo_schedule(lob, start_date, end_date, emp_ids):
    """Generate schedule for demo users (synchronous, fast)."""
    from app.demo_data import plan_demo_day, get_demo_requirements
    days = []
    d = start_date
    total_shifts = 0
    total_hours = 0.0
    cov_sum, cov_days = 0.0, 0
    never = {}
    demo_emp_filter = set(str(e) for e in emp_ids) if emp_ids else None
    while d <= end_date:
        all_scheds, warnings = plan_demo_day(d, lob)
        if demo_emp_filter:
            all_scheds = [s for s in all_scheds
                          if str(s.get("employee_id", "")) in demo_emp_filter]
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
    return {
        "lob": lob, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "days": days,
        "totals": {"total_shifts": total_shifts, "total_hours": round(total_hours, 1),
                   "total_days": len(days),
                   "avg_coverage_pct": round(cov_sum / cov_days, 1) if cov_days else 0},
        "warnings": global_warnings,
    }


def _do_generate(lob, start_date, end_date, shift_hrs, emp_ids, mode):
    """Run schedule generation (called from background thread)."""
    from app.scheduling.engine import generate_schedule_range

    sheet = _get_sheet()

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
        return generate_schedule_range(lob, start_date, end_date, shift_hrs, sheet, emp_ids)

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
    return {
        "lob": lob, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "days": days,
        "totals": {"total_shifts": total_shifts, "total_hours": round(total_hours, 1),
                   "total_days": len(days),
                   "avg_coverage_pct": round(cov_sum / cov_days, 1) if cov_days else 0},
        "warnings": [],
    }



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
                t = r["time"][11:16] if len(r["time"]) > 5 else r["time"]  # "YYYY-MM-DD HH:MM" → "HH:MM"
                t_min = int(t[:2]) * 60 + int(t[3:])
                scheduled = 0
                for s in shifts:
                    s_start = s.get("start", "")
                    s_end = s.get("end", "")
                    if s_start and s_end:
                        s_min = int(s_start[:2]) * 60 + int(s_start[3:])
                        e_min = int(s_end[:2]) * 60 + int(s_end[3:])
                        if s_min <= t_min < e_min:
                            scheduled += 1
                req = r["agents_required"]
                gap = scheduled - req
                pct = round(scheduled / req * 100, 1) if req > 0 else (100.0 if scheduled > 0 else 0)
                cov.append({
                    "time": t,
                    "required": req,
                    "scheduled": scheduled,
                    "gap": gap,
                    "coverage_pct": pct,
                })
            total_req = sum(c["required"] for c in cov)
            total_sched = sum(c["scheduled"] for c in cov)
            avg_pct = round(sum(c["coverage_pct"] for c in cov) / max(1, len(cov)), 1)
            summary = {
                "avg_coverage_pct": avg_pct,
                "peak_required": max((c["required"] for c in cov), default=0),
                "peak_gap": min((c["gap"] for c in cov), default=0),
                "understaffed_intervals": sum(1 for c in cov if c["gap"] < 0),
                "total_intervals": len(cov),
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

        # Delete existing schedules for this LOB+date (bulk)
        Schedule.query.filter_by(
            schedule_date=sched_date, planning_unit_id=pu_id
        ).delete(synchronize_session=False)

        # Prefetch all employees by external ID for this batch
        ext_ids = [str(s.get("employee_id", "")).strip() for s in shifts if s.get("employee_id")]
        emp_lookup = {}
        if ext_ids:
            for emp in Employee.query.filter(Employee.employee_id.in_(ext_ids)).all():
                emp_lookup[emp.employee_id] = emp

        saved = 0
        for shift in shifts:
            emp_ext_id = str(shift.get("employee_id", "")).strip()
            if not emp_ext_id:
                continue

            emp = emp_lookup.get(emp_ext_id)
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

        # Notify admins about saved schedule
        try:
            from app.routes.notifications import notify_all_admins
            notify_all_admins(
                f"Schedule saved: {lob}",
                f"{saved} shift(s) saved for {date_str}.",
                category="schedule",
                link="/scheduling/",
            )
        except Exception:
            pass  # notifications are best-effort

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


@scheduling_bp.route("/shift/<int:shift_id>/recalc-oncall", methods=["POST"])
@login_required
def recalc_oncall(shift_id):
    """Recalculate on-call segments for a shift.

    Deletes all existing on-call segments, then fills the gaps between
    non-on-call segments (breaks, lunches, meetings, etc.) with new
    on-call segments that span the full shift.
    """
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "segments": []})
    try:
        sched = Schedule.query.get(shift_id)
        if not sched:
            return jsonify({"success": False, "error": "Shift not found"})

        shift_start = sched.shift_start.hour * 60 + sched.shift_start.minute
        shift_end = sched.shift_end.hour * 60 + sched.shift_end.minute

        # Get all segments for this shift
        all_segs = ShiftSegment.query.filter_by(schedule_id=shift_id).order_by(
            ShiftSegment.start_time).all()

        # Separate on-call from non-on-call
        non_oncall = [s for s in all_segs if s.activity_type != "on-call"]
        oncall_ids = [s.id for s in all_segs if s.activity_type == "on-call"]

        # Delete existing on-call segments
        if oncall_ids:
            ShiftSegment.query.filter(ShiftSegment.id.in_(oncall_ids)).delete(
                synchronize_session="fetch")

        # Build list of occupied intervals (non-on-call)
        occupied = sorted(
            [(s.start_time.hour * 60 + s.start_time.minute,
              s.end_time.hour * 60 + s.end_time.minute) for s in non_oncall],
            key=lambda x: x[0]
        )

        # Fill gaps with on-call segments
        new_segs = []
        cursor = shift_start
        order = 0
        for occ_start, occ_end in occupied:
            if cursor < occ_start:
                sh, sm = divmod(cursor, 60)
                eh, em = divmod(occ_start, 60)
                seg = ShiftSegment(
                    schedule_id=shift_id,
                    activity_type="on-call",
                    start_time=_parse_time(f"{sh:02d}:{sm:02d}"),
                    end_time=_parse_time(f"{eh:02d}:{em:02d}"),
                    duration_mins=occ_start - cursor,
                    sort_order=order,
                )
                db.session.add(seg)
                new_segs.append(seg)
                order += 1
            cursor = max(cursor, occ_end)

        # Fill from last occupied to shift end
        if cursor < shift_end:
            sh, sm = divmod(cursor, 60)
            eh, em = divmod(shift_end, 60)
            seg = ShiftSegment(
                schedule_id=shift_id,
                activity_type="on-call",
                start_time=_parse_time(f"{sh:02d}:{sm:02d}"),
                end_time=_parse_time(f"{eh:02d}:{em:02d}"),
                duration_mins=shift_end - cursor,
                sort_order=order,
            )
            db.session.add(seg)
            new_segs.append(seg)

        # Re-sort all segments
        all_remaining = ShiftSegment.query.filter_by(schedule_id=shift_id).order_by(
            ShiftSegment.start_time).all()
        for i, s in enumerate(all_remaining):
            s.sort_order = i

        db.session.commit()

        return jsonify({
            "success": True,
            "segments": [s.to_dict() for s in
                         ShiftSegment.query.filter_by(schedule_id=shift_id).order_by(
                             ShiftSegment.start_time).all()]
        })

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


@scheduling_bp.route("/export-xlsx", methods=["POST"])
@login_required
def export_xlsx():
    """Export schedule to Excel (.xlsx) with formatting."""
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

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Schedule"

        # Header styling
        hdr_font = Font(name="Arial", bold=True, size=10, color="FFFFFF")
        hdr_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        hdr_align = Alignment(horizontal="center", vertical="center")
        thin = Side(style="thin", color="D9D9D9")
        cell_border = Border(bottom=thin)

        headers = ["Date", "Day", "Employee", "Employee ID", "Shift Start",
                    "Shift End", "Hours", "Type", "Status"]
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=col, value=h)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = hdr_align

        row = 2
        for s in schedules:
            d = s.to_dict()
            sched_date = s.schedule_date
            ws.cell(row=row, column=1, value=sched_date.strftime("%Y-%m-%d"))
            ws.cell(row=row, column=2, value=sched_date.strftime("%a"))
            ws.cell(row=row, column=3, value=d["employee"])
            ws.cell(row=row, column=4, value=d["employee_id"])
            ws.cell(row=row, column=5, value=d["start"])
            ws.cell(row=row, column=6, value=d["end"])
            ws.cell(row=row, column=7, value=d["hours"])
            ws.cell(row=row, column=8, value=d["type"])
            ws.cell(row=row, column=9, value=d["status"])
            for col in range(1, 10):
                ws.cell(row=row, column=col).border = cell_border
                ws.cell(row=row, column=col).font = Font(name="Arial", size=10)
            row += 1

        # Auto-width
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 3, 25)

        # Freeze header
        ws.freeze_panes = "A2"

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"schedule_{lob}_{start_str}_to_{end_str}.xlsx"
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        log.exception("Excel export error")
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

        count = Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= start_date,
            Schedule.schedule_date <= end_date,
        ).delete(synchronize_session=False)
        db.session.commit()

        return jsonify({"success": True, "deleted": count})

    except Exception as e:
        log.exception("Clear schedule error")
        return jsonify({"success": False, "error": str(e)})


# ── Copy Week ─────────────────────────────────────────────
@scheduling_bp.route("/copy-week", methods=["POST"])
@login_required
def copy_week():
    """
    Copy an entire week's schedule to a target week.
    POST JSON: {lob, source_start, target_start}
    Both dates should be Mondays (start of week). Copies 7 days.
    """
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        source_start_str = payload.get("source_start", "")
        target_start_str = payload.get("target_start", "")

        if not lob or not source_start_str or not target_start_str:
            return jsonify({"success": False, "error": "LOB, source_start, and target_start required"})

        source_start = datetime.datetime.strptime(source_start_str, "%Y-%m-%d").date()
        target_start = datetime.datetime.strptime(target_start_str, "%Y-%m-%d").date()
        source_end = source_start + datetime.timedelta(days=6)
        target_end = target_start + datetime.timedelta(days=6)
        day_offset = (target_start - source_start).days

        user = get_current_user()
        if user and user.get("is_demo"):
            return jsonify({"success": True, "copied": 0, "demo": True,
                            "message": "Week copied (demo mode)."})

        from app.models import PlanningUnit, ShiftSegment
        pu = PlanningUnit.query.filter(
            db.func.lower(PlanningUnit.name) == lob.lower()
        ).first()
        if not pu:
            return jsonify({"success": False, "error": "Planning unit not found"})

        # Delete existing schedules in target range
        Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= target_start,
            Schedule.schedule_date <= target_end,
        ).delete(synchronize_session=False)

        # Load source schedules with segments
        source_schedules = Schedule.query.filter(
            Schedule.planning_unit_id == pu.id,
            Schedule.schedule_date >= source_start,
            Schedule.schedule_date <= source_end,
        ).all()

        copied = 0
        for sched in source_schedules:
            new_date = sched.schedule_date + datetime.timedelta(days=day_offset)
            new_sched = Schedule(
                employee_id=sched.employee_id,
                planning_unit_id=sched.planning_unit_id,
                schedule_date=new_date,
                shift_start=sched.shift_start,
                shift_end=sched.shift_end,
                shift_type=sched.shift_type,
                hours=sched.hours,
                status=sched.status,
            )
            db.session.add(new_sched)
            db.session.flush()  # get new_sched.id

            for seg in sched.segments:
                new_seg = ShiftSegment(
                    schedule_id=new_sched.id,
                    activity_type=seg.activity_type,
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    duration_mins=seg.duration_mins,
                    sort_order=seg.sort_order,
                    notes=seg.notes,
                )
                db.session.add(new_seg)
            copied += 1

        db.session.commit()
        return jsonify({"success": True, "copied": copied,
                        "message": f"Copied {copied} schedule(s) to target week."})

    except Exception as e:
        db.session.rollback()
        log.exception("Copy week error")
        return jsonify({"success": False, "error": str(e)})


# ── Employees for LOB (API) ───────────────────────────────
@scheduling_bp.route("/employees", methods=["POST"])
@login_required
def employees_for_lob():
    """Return employees filtered by LOB for the employee picker."""
    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()

        selection_id = payload.get("selection_id", "")
        skill_id = payload.get("skill_id", "")
        shift_sequence_id = payload.get("shift_sequence_id", "")

        user = get_current_user()
        if user and user.get("is_demo"):
            from app.demo_data import DEMO_EMPLOYEES, get_demo_selections, get_demo_skills_config
            # Build filter sets for demo mode
            sel_members = set()
            if selection_id:
                sel = get_demo_selections()
                for s in sel:
                    if str(s["id"]) == str(selection_id):
                        # In demo mode, assign members by index
                        sel_members = {str(i+1) for i in range(s.get("member_count", 0))}
                        break

            emps = []
            for e in DEMO_EMPLOYEES:
                if lob and e.get("Latest Skill Name", "") != lob:
                    continue
                if str(e.get("Status", "")).strip().lower() in ("inactive", "terminated"):
                    continue
                eid = str(e.get("Employee ID", ""))
                if selection_id and eid not in sel_members:
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


# ── Import schedules from connected WFM system ───────────────
@scheduling_bp.route("/import/wfm", methods=["POST"])
@scheduling_bp.route("/import/wfm-api", methods=["POST"])  # backwards compat
@login_required
def import_wfm():
    """POST JSON: {lob?, start_date, end_date}
    Pulls schedules from the connected WFM system into Serevo for the range.
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
        import threading

        # Check if a background import is already running
        if getattr(cp, '_import_running', False):
            status = getattr(cp, '_import_status', {})
            return jsonify({"success": False,
                            "error": f"An import is already in progress. {status.get('message', '')}"})

        # Reset status BEFORE starting the thread so polls don't see old results
        cp._import_running = True
        cp._import_status = {"message": "Fetching schedules from API...", "done": False}

        # Run the import in a background thread to avoid Render's 30s timeout.
        # ~450 employees × 7 days = ~3,150 API calls which takes several minutes.
        def _bg_import(app, sd, ed):
            with app.app_context():
                try:
                    shifts = cp.fetch_pw_schedules(sd, ed)
                    if not shifts:
                        cp._import_status = {
                            "message": "Completed — no schedules found for the selected date range.",
                            "done": True, "success": False,
                        }
                        return
                    created, replaced, skipped = cp.upsert_pw_schedules(shifts, sd, ed)
                    msg = f"Imported {created} shift(s)"
                    if replaced:
                        msg += f", replaced {replaced} existing"
                    if skipped:
                        msg += f", skipped {skipped} unknown employees"
                    cp._import_status = {"message": msg, "done": True, "success": True,
                                         "created": created, "replaced": replaced,
                                         "skipped": skipped}
                except Exception as e:
                    log.exception("Background WFM import failed")
                    cp._import_status = {"message": f"Import failed: {e}",
                                         "done": True, "success": False}
                finally:
                    cp._import_running = False

        from flask import current_app
        t = threading.Thread(target=_bg_import, args=(current_app._get_current_object(),
                                                       start_date, end_date), daemon=True)
        t.start()
        return jsonify({"success": True,
                        "message": "Schedule import started in the background. "
                                   "This may take a few minutes — check back shortly.",
                        "poll": True})
    except Exception as e:
        log.exception("WFM schedule import error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/import/wfm/status", methods=["GET"])
@login_required
def import_wfm_status():
    """Poll for background import status."""
    from app.capacity import planning as cp
    running = getattr(cp, '_import_running', False)
    status = getattr(cp, '_import_status', {})
    return jsonify({"running": running, **status})


@scheduling_bp.route("/import/wfm/reset", methods=["POST"])
@admin_required
def import_wfm_reset():
    """Force-clear a stuck import flag so a new import can start."""
    from app.capacity import planning as cp
    cp._import_running = False
    cp._import_status = {"message": "Import cancelled by admin.", "done": True, "success": False}
    return jsonify({"success": True, "message": "Import flag cleared."})


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


# ═══════════════════════════════════════════════════════════════
# MASS SEGMENT UPDATE
# ═══════════════════════════════════════════════════════════════

def _time_to_mins(t):
    """Convert 'HH:MM' string to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _mins_to_time(m):
    """Convert minutes since midnight to 'HH:MM'."""
    return f"{m // 60:02d}:{m % 60:02d}"


@scheduling_bp.route("/mass-segment/suggest", methods=["POST"])
@login_required
def mass_segment_suggest():
    """
    Suggest optimal segment placements for multiple shifts based on coverage.

    POST JSON: {
        shift_ids: [int],         # shifts to place segments on
        activity_type: str,       # e.g. 'break', 'lunch', 'meeting'
        duration_mins: int,       # segment length in minutes
        window_start: 'HH:MM',   # earliest allowed start
        window_end: 'HH:MM',     # latest allowed end
        coverage: [{time, required, scheduled, gap}]  # from current day's data
    }

    Returns {suggestions: [{shift_id, employee, start, end, coverage_impact}]}
    """
    try:
        payload = request.get_json(silent=True) or {}
        shift_ids = payload.get("shift_ids", [])
        activity = payload.get("activity_type", "break")
        duration = int(payload.get("duration_mins", 15))
        win_start = payload.get("window_start", "10:00")
        win_end = payload.get("window_end", "14:00")
        coverage = payload.get("coverage", [])

        if not shift_ids:
            return jsonify({"success": False, "error": "No shifts selected"})

        win_s = _time_to_mins(win_start)
        win_e = _time_to_mins(win_end)

        if win_e - win_s < duration:
            return jsonify({"success": False, "error": "Window too small for segment duration"})

        # Build a coverage map: minute -> {required, scheduled}
        cov_map = {}
        for c in coverage:
            t = c.get("time", "")
            if len(t) >= 5:
                m = _time_to_mins(t[:5])
                cov_map[m] = {"req": c.get("required", 0), "sched": c.get("scheduled", 0)}

        # Determine interval step (usually 15 or 30 min)
        cov_times = sorted(cov_map.keys())
        interval = 15
        if len(cov_times) >= 2:
            interval = cov_times[1] - cov_times[0]
        if interval < 1:
            interval = 15

        # Load shift info
        shifts = Schedule.query.filter(Schedule.id.in_(shift_ids)).all()
        if not shifts:
            return jsonify({"success": False, "error": "No matching shifts found"})

        # For each shift, find the time slot within the window where pulling
        # that person off-line has the LEAST coverage impact.
        # We score each candidate slot by the MINIMUM surplus across its intervals.
        # Higher minimum surplus = safer to remove someone there.
        suggestions = []
        already_placed = []  # track placements so we stagger

        for shift in shifts:
            s_start = shift.shift_start
            s_end = shift.shift_end
            if not s_start or not s_end:
                continue
            s_s = s_start.hour * 60 + s_start.minute
            s_e = s_end.hour * 60 + s_end.minute

            # Effective window: intersection of requested window and shift times
            eff_start = max(win_s, s_s)
            eff_end = min(win_e, s_e)
            if eff_end - eff_start < duration:
                continue

            best_slot = None
            best_score = -9999

            # Try every possible start in interval steps
            t = eff_start
            while t + duration <= eff_end:
                # Calculate worst-case surplus if we pull this person at time t
                min_surplus = 9999
                overlap_count = 0
                m = t
                while m < t + duration:
                    cov = cov_map.get(m)
                    if cov:
                        surplus = cov["sched"] - cov["req"]
                        # Subtract anyone we've already placed in this interval
                        for placed in already_placed:
                            if placed[0] <= m < placed[1]:
                                surplus -= 1
                        min_surplus = min(min_surplus, surplus)
                    for placed in already_placed:
                        if placed[0] <= m < placed[1]:
                            overlap_count += 1
                    m += interval

                if min_surplus == 9999:
                    min_surplus = -overlap_count  # stagger when no coverage data

                if min_surplus > best_score:
                    best_score = min_surplus
                    best_slot = t

                t += interval

            if best_slot is not None:
                seg_end = best_slot + duration
                already_placed.append((best_slot, seg_end))
                emp = shift.employee
                suggestions.append({
                    "shift_id": shift.id,
                    "employee": emp.full_name if emp else f"ID {shift.employee_id}",
                    "start": _mins_to_time(best_slot),
                    "end": _mins_to_time(seg_end),
                    "coverage_impact": best_score,
                })

        return jsonify({"success": True, "suggestions": suggestions})

    except Exception as e:
        log.exception("Mass segment suggest error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/mass-segment/apply", methods=["POST"])
@login_required
def mass_segment_apply():
    """
    Apply segments to multiple shifts at once.

    POST JSON: {
        segments: [{shift_id, activity_type, start, end, notes?}]
    }
    """
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "applied": 0,
                        "message": "Demo mode — changes kept on screen only"})
    try:
        payload = request.get_json(silent=True) or {}
        items = payload.get("segments", [])
        if not items:
            return jsonify({"success": False, "error": "No segments to apply"})

        applied = 0
        for item in items:
            shift_id = item.get("shift_id")
            if not shift_id:
                continue
            sched = Schedule.query.get(shift_id)
            if not sched:
                continue

            start_t = _parse_time(item.get("start", ""))
            end_t = _parse_time(item.get("end", ""))
            if not start_t or not end_t:
                continue

            dur = (end_t.hour * 60 + end_t.minute) - (start_t.hour * 60 + start_t.minute)
            max_order = max((s.sort_order for s in sched.segments), default=-1) + 1

            seg = ShiftSegment(
                schedule_id=sched.id,
                activity_type=item.get("activity_type", "break"),
                start_time=start_t,
                end_time=end_t,
                duration_mins=max(dur, 0),
                sort_order=max_order,
                notes=item.get("notes", ""),
            )
            db.session.add(seg)
            applied += 1

        db.session.commit()
        return jsonify({"success": True, "applied": applied})

    except Exception as e:
        db.session.rollback()
        log.exception("Mass segment apply error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/optimize-segments", methods=["POST"])
@login_required
def optimize_segments():
    """
    Re-position break and lunch segments across shifts for a day to
    minimise coverage impact.  Reads existing flexible segments, removes
    them, then re-places them using a greedy best-surplus algorithm with
    stagger tracking so no two employees are on break at the same time
    when avoidable.

    POST JSON: {
        shift_ids: [int],                 # shifts to optimise
        coverage: [{time, required, scheduled, gap}],  # day's coverage
        activity_types: [str]             # which segment types to reposition
                                          #   default: ['break','lunch']
    }
    Returns {
        success: true,
        changes: [{shift_id, employee, type, old_start, old_end,
                   new_start, new_end, coverage_impact}]
    }
    """
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True, "changes": [],
                        "message": "Demo mode — changes kept on screen only"})
    try:
        payload = request.get_json(silent=True) or {}
        shift_ids = payload.get("shift_ids", [])
        coverage = payload.get("coverage", [])
        types_to_opt = set(payload.get("activity_types", ["break", "lunch"]))

        if not shift_ids:
            return jsonify({"success": False, "error": "No shifts selected"})

        # Build coverage map
        cov_map = {}
        for c in coverage:
            t = c.get("time", "")
            if len(t) >= 5:
                m = _time_to_mins(t[:5])
                cov_map[m] = {"req": c.get("required", 0), "sched": c.get("scheduled", 0)}

        cov_times = sorted(cov_map.keys())
        interval = 15
        if len(cov_times) >= 2:
            interval = cov_times[1] - cov_times[0]
        if interval < 1:
            interval = 15

        # Load shifts and their segments
        shifts = Schedule.query.filter(Schedule.id.in_(shift_ids)).all()
        if not shifts:
            return jsonify({"success": False, "error": "No matching shifts found"})

        # Collect all segments that we'll reposition, and track ones we won't
        # move (so they count as "already placed" for coverage)
        to_reposition = []   # [(shift, segment, shift_start_min, shift_end_min)]
        fixed_placed = []    # [(start_min, end_min)]

        for shift in shifts:
            s_start = shift.shift_start
            s_end = shift.shift_end
            if not s_start or not s_end:
                continue
            s_s = s_start.hour * 60 + s_start.minute
            s_e = s_end.hour * 60 + s_end.minute

            for seg in shift.segments:
                if seg.activity_type in types_to_opt:
                    to_reposition.append((shift, seg, s_s, s_e))
                elif seg.activity_type != "on-call":
                    # Fixed segments (meetings, training, etc.) reduce coverage
                    if seg.start_time and seg.end_time:
                        fs = seg.start_time.hour * 60 + seg.start_time.minute
                        fe = seg.end_time.hour * 60 + seg.end_time.minute
                        fixed_placed.append((fs, fe))

        if not to_reposition:
            return jsonify({"success": True, "changes": [],
                            "message": "No break/lunch segments found to optimise"})

        # Get segment rules for window constraints (per shift type)
        from app.scheduling.engine import _get_segment_rules
        _rules_cache = {}  # shift_type -> {seg_type -> rule}

        def _rules_for(shift_type):
            if shift_type not in _rules_cache:
                rules = _get_segment_rules(shift_type or "full")
                by_type = {}
                for r in rules:
                    if r["type"] not in by_type:
                        by_type[r["type"]] = r
                _rules_cache[shift_type] = by_type
            return _rules_cache[shift_type]

        # Build per-shift fixed segments (meetings, training, etc.) that must not overlap
        shift_fixed = {}  # shift.id -> [(start_min, end_min)]
        for shift in shifts:
            fixed = []
            for seg in shift.segments:
                if seg.activity_type not in types_to_opt and seg.activity_type != "on-call":
                    if seg.start_time and seg.end_time:
                        fs = seg.start_time.hour * 60 + seg.start_time.minute
                        fe = seg.end_time.hour * 60 + seg.end_time.minute
                        fixed.append((fs, fe))
            shift_fixed[shift.id] = fixed
            if fixed:
                emp = shift.employee
                log.info(f"Optimizer: shift {shift.id} ({emp.full_name if emp else '?'}) "
                         f"has {len(fixed)} fixed segments: {fixed}")

        # Greedy placement: process each segment, find best slot
        already_placed = list(fixed_placed)
        changes = []

        for shift, seg, s_s, s_e in to_reposition:
            duration = seg.duration_mins or 15
            rules_by_type = _rules_for(shift.shift_type)
            rule = rules_by_type.get(seg.activity_type)

            # Determine window: use rule window relative to shift start,
            # or fall back to full shift
            if rule and rule.get("is_flexible"):
                win_s = s_s + (rule.get("window_start_mins", 0))
                win_e = s_s + (rule.get("window_end_mins", s_e - s_s))
            else:
                # Non-flexible segment — still try to optimise within
                # a ±30 minute window of its current position
                if seg.start_time:
                    cur_start = seg.start_time.hour * 60 + seg.start_time.minute
                    win_s = max(s_s, cur_start - 30)
                    win_e = min(s_e, cur_start + 30 + duration)
                else:
                    win_s = s_s
                    win_e = s_e

            # Clamp to shift bounds
            win_s = max(win_s, s_s)
            win_e = min(win_e, s_e)
            if win_e - win_s < duration:
                continue  # can't fit

            # Find best slot
            best_slot = None
            best_score = -9999

            t = win_s
            while t + duration <= win_e:
                # Skip slots that overlap fixed segments on this shift
                overlaps_fixed = False
                for fs, fe in shift_fixed.get(shift.id, []):
                    if t < fe and t + duration > fs:
                        overlaps_fixed = True
                        break
                if overlaps_fixed:
                    t += interval
                    continue

                min_surplus = 9999
                overlap_count = 0
                m = t
                while m < t + duration:
                    cov = cov_map.get(m)
                    if cov:
                        surplus = cov["sched"] - cov["req"]
                        for placed in already_placed:
                            if placed[0] <= m < placed[1]:
                                surplus -= 1
                        min_surplus = min(min_surplus, surplus)
                    # Count overlaps with already-placed segments even without coverage
                    for placed in already_placed:
                        if placed[0] <= m < placed[1]:
                            overlap_count += 1
                    m += interval
                if min_surplus == 9999:
                    # No coverage data — use negative overlap count to spread breaks apart
                    min_surplus = -overlap_count
                if min_surplus > best_score:
                    best_score = min_surplus
                    best_slot = t
                t += interval

            if best_slot is None:
                continue

            old_start = seg.start_time.strftime("%H:%M") if seg.start_time else ""
            old_end = seg.end_time.strftime("%H:%M") if seg.end_time else ""
            new_start_str = _mins_to_time(best_slot)
            new_end_str = _mins_to_time(best_slot + duration)

            already_placed.append((best_slot, best_slot + duration))

            # Skip no-op changes (same position)
            if old_start == new_start_str and old_end == new_end_str:
                continue

            # Update the segment in the DB
            import datetime as _dt
            seg.start_time = _dt.time(best_slot // 60, best_slot % 60)
            seg.end_time = _dt.time((best_slot + duration) // 60, (best_slot + duration) % 60)

            emp = shift.employee
            changes.append({
                "shift_id": shift.id,
                "segment_id": seg.id,
                "employee": emp.full_name if emp else f"ID {shift.employee_id}",
                "type": seg.activity_type,
                "old_start": old_start,
                "old_end": old_end,
                "new_start": new_start_str,
                "new_end": new_end_str,
                "coverage_impact": best_score,
            })

        db.session.commit()
        log.info(f"Optimizer: {len(changes)} changes made out of {len(to_reposition)} segments")
        for c in changes:
            log.info(f"  {c['employee']} {c['type']}: {c['old_start']}-{c['old_end']} -> {c['new_start']}-{c['new_end']}")
        return jsonify({"success": True, "changes": changes})

    except Exception as e:
        db.session.rollback()
        log.exception("Optimize segments error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/mass-segment/cross-lob-shifts", methods=["POST"])
@login_required
def mass_segment_cross_lob_shifts():
    """
    Fetch shifts across multiple LOBs for a given date, for the cross-LOB mass update.

    POST JSON: { lobs: [str], date: 'YYYY-MM-DD' }
    Returns { shifts: [{id, employee, start, end, lob}], coverage: {...} }
    """
    try:
        payload = request.get_json(silent=True) or {}
        lobs = payload.get("lobs", [])
        date_str = payload.get("date", "")
        if not lobs or not date_str:
            return jsonify({"success": False, "error": "LOBs and date are required"})

        import datetime as _dt
        target_date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()

        from app.models import PlanningUnit
        # Get planning unit IDs for the requested LOBs
        pu_ids = []
        for lob_name in lobs:
            pu = PlanningUnit.query.filter(db.func.lower(PlanningUnit.name) == lob_name.lower()).first()
            if pu:
                pu_ids.append(pu.id)

        if not pu_ids:
            return jsonify({"success": True, "shifts": []})

        # Find all scheduled shifts on the target date for those planning units
        shifts = Schedule.query.filter(
            Schedule.schedule_date == target_date,
            Schedule.planning_unit_id.in_(pu_ids),
            Schedule.shift_type.in_(["scheduled", "regular"]),
        ).all()

        results = []
        for s in shifts:
            emp = s.employee
            pu = PlanningUnit.query.get(s.planning_unit_id) if s.planning_unit_id else None
            results.append({
                "id": s.id,
                "employee": emp.full_name if emp else f"ID {s.employee_id}",
                "start": s.shift_start.strftime("%H:%M") if s.shift_start else "",
                "end": s.shift_end.strftime("%H:%M") if s.shift_end else "",
                "lob": pu.name if pu else "Unknown",
                "team_lead": emp.team_lead if emp and emp.team_lead else "",
            })

        # Also build aggregated coverage data for the day across all requested LOBs
        coverage = []
        try:
            from app.models import RequirementInterval
            reqs = RequirementInterval.query.filter(
                RequirementInterval.interval_date == target_date,
                RequirementInterval.planning_unit_id.in_(pu_ids),
            ).all()
            # Aggregate by time
            cov_map = {}
            for r in reqs:
                t_str = r.interval_time.strftime("%H:%M") if r.interval_time else ""
                if t_str not in cov_map:
                    cov_map[t_str] = {"time": t_str, "required": 0, "scheduled": 0}
                cov_map[t_str]["required"] += r.required or 0
                cov_map[t_str]["scheduled"] += r.scheduled or 0
            coverage = sorted(cov_map.values(), key=lambda x: x["time"])
        except Exception as e:
            log.warning("Coverage data unavailable: %s", e)

        return jsonify({"success": True, "shifts": results, "coverage": coverage})

    except Exception as e:
        log.exception("Cross-LOB shifts error")
        return jsonify({"success": False, "error": str(e)})


# ═══════════════════════════════════════════════════════════════
# BULK SHIFT EDITING
# ═══════════════════════════════════════════════════════════════

@scheduling_bp.route("/bulk-edit", methods=["POST"])
@login_required
def bulk_edit():
    """
    Apply the same change to multiple shifts at once.

    POST JSON: {
        shift_ids: [int],
        action: "change_time" | "change_activity" | "remove_shifts" | "add_day_off",
        params: {
            // for change_time:
            new_start: "HH:MM",
            new_end: "HH:MM",
            // for change_activity:
            activity_type: str,
            activity_start: "HH:MM",
            activity_end: "HH:MM",
            notes: str,
        }
    }
    """
    _u = get_current_user()
    if _u and _u.get("is_demo"):
        return jsonify({"success": True, "demo": True,
                        "message": "Demo mode — changes not saved",
                        "affected": 0})
    try:
        payload = request.get_json(silent=True) or {}
        shift_ids = payload.get("shift_ids", [])
        action = payload.get("action", "")
        params = payload.get("params", {})

        if not shift_ids:
            return jsonify({"success": False, "error": "No shifts selected"})
        if not action:
            return jsonify({"success": False, "error": "No action specified"})

        shifts = Schedule.query.filter(Schedule.id.in_(shift_ids)).all()
        if not shifts:
            return jsonify({"success": False, "error": "No matching shifts found"})

        affected = 0

        if action == "change_time":
            new_start = _parse_time(params.get("new_start"))
            new_end = _parse_time(params.get("new_end"))
            if not new_start or not new_end:
                return jsonify({"success": False, "error": "Start and end times required"})
            for s in shifts:
                s.shift_start = new_start
                s.shift_end = new_end
                s.hours = _time_diff_hours(new_start, new_end)
                affected += 1

        elif action == "change_activity":
            act_type = params.get("activity_type", "break")
            act_start = _parse_time(params.get("activity_start"))
            act_end = _parse_time(params.get("activity_end"))
            notes = params.get("notes", "")
            if not act_start or not act_end:
                return jsonify({"success": False, "error": "Activity start/end required"})
            dur = (act_end.hour * 60 + act_end.minute) - (act_start.hour * 60 + act_start.minute)
            for s in shifts:
                seg = ShiftSegment(
                    schedule_id=s.id,
                    activity_type=act_type,
                    start_time=act_start,
                    end_time=act_end,
                    duration_mins=dur,
                    notes=notes,
                )
                db.session.add(seg)
                affected += 1

        elif action == "remove_shifts":
            for s in shifts:
                # Remove segments first
                ShiftSegment.query.filter_by(schedule_id=s.id).delete()
                db.session.delete(s)
                affected += 1

        elif action == "add_day_off":
            for s in shifts:
                s.shift_type = "day_off"
                s.shift_start = None
                s.shift_end = None
                s.hours = 0
                # Remove segments
                ShiftSegment.query.filter_by(schedule_id=s.id).delete()
                affected += 1

        else:
            return jsonify({"success": False, "error": f"Unknown action: {action}"})

        db.session.commit()
        return jsonify({"success": True, "affected": affected})

    except Exception as e:
        db.session.rollback()
        log.exception("Bulk edit error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/score", methods=["POST"])
@login_required
def get_schedule_score():
    """
    Score the current schedule.

    POST JSON: { days: [{date, shifts: [...], coverage: [...]}] }
    OR: { schedule_date: "YYYY-MM-DD", lob: "..." } to score from DB
    """
    try:
        from app.scheduling.engine import score_schedule
        from app.models import PlanningUnit
        payload = request.get_json(silent=True) or {}

        days_data = payload.get("days")
        if days_data:
            result = score_schedule(days_data, payload.get("lob"))
            return jsonify({"success": True, "score": result})

        # Score from DB
        lob = payload.get("lob")
        start = payload.get("start_date")
        end = payload.get("end_date")
        if not lob or not start:
            return jsonify({"success": False, "error": "Provide days data or lob+start_date"})

        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end or start, "%Y-%m-%d").date()

        # Load saved schedule
        pu = PlanningUnit.query.filter_by(name=lob).first()
        sched_filter = [
            Schedule.schedule_date >= start_dt,
            Schedule.schedule_date <= end_dt,
        ]
        if pu:
            sched_filter.append(Schedule.planning_unit_id == pu.id)
        schedules = Schedule.query.filter(*sched_filter).all()

        if not schedules:
            return jsonify({"success": False, "error": "No saved schedule found"})

        # Group by date
        by_date = defaultdict(list)
        for s in schedules:
            d = s.schedule_date.strftime("%Y-%m-%d")
            emp = s.employee
            by_date[d].append({
                "employee": emp.full_name if emp else "",
                "employee_id": emp.employee_id if emp else "",
                "start": s.shift_start.strftime("%H:%M") if s.shift_start else "08:00",
                "end": s.shift_end.strftime("%H:%M") if s.shift_end else "16:30",
                "hours": s.hours or 0,
                "type": s.shift_type or "full",
                "effectiveness": 1.0,
            })

        days = []
        for date_str in sorted(by_date.keys()):
            days.append({
                "date": date_str,
                "shifts": by_date[date_str],
                "coverage": [],  # would need requirements to fill
            })

        result = score_schedule(days, lob)
        return jsonify({"success": True, "score": result})

    except Exception as e:
        log.exception("Score error")
        return jsonify({"success": False, "error": str(e)})


@scheduling_bp.route("/publish", methods=["POST"])
@login_required
def publish_schedule():
    """Publish a week's schedule — sends in-app notifications to affected agents."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(success=False, error="Forbidden"), 403

    payload = request.get_json(silent=True) or {}
    lob = payload.get("lob")
    start = payload.get("start_date")
    end = payload.get("end_date")

    if not start:
        return jsonify(success=False, error="start_date is required")

    try:
        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end or start, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(success=False, error="Invalid date format")

    from app.models import db, Schedule, Employee, PlanningUnit, Notification, User

    q = Schedule.query.filter(
        Schedule.schedule_date >= start_dt,
        Schedule.schedule_date <= end_dt,
    )
    if lob:
        pu = PlanningUnit.query.filter_by(name=lob).first()
        if pu:
            q = q.filter_by(planning_unit_id=pu.id)

    schedules = q.all()
    if not schedules:
        return jsonify(success=False, error="No schedules found for this period")

    # Collect unique employee IDs
    emp_ids = set(s.employee_id for s in schedules)
    notified = 0

    for emp_id in emp_ids:
        emp = Employee.query.get(emp_id)
        if not emp:
            continue
        # Find the user account linked to this employee
        linked_user = User.query.filter_by(employee_id=emp.id).first()
        if not linked_user:
            continue
        notif = Notification(
            user_id=linked_user.id,
            category="schedule",
            title="Schedule Published",
            message=f"Your schedule for {start_dt.strftime('%b %d')} – {end_dt.strftime('%b %d')} has been published.",
            link="/my-schedule/",
        )
        db.session.add(notif)
        notified += 1

    db.session.commit()
    return jsonify(success=True, notified=notified, total_shifts=len(schedules))
