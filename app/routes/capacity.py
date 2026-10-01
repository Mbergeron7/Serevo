"""
routes/capacity.py — Capacity Planning blueprint
=================================================
Control panel, data refresh endpoints, and capacity plan view.
"""

import time
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import requests

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify)
from app.auth import login_required, get_current_user
from app.capacity import planning as cp
from config import cfg

log = logging.getLogger("serevo.capacity")

capacity_bp = Blueprint("capacity", __name__, url_prefix="/capacity")


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    from app.auth import get_current_user
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")
    return None

TIMEZONE = cp.TIMEZONE


# ── helpers to get Google Sheet worksheets ─────────────────────
from app.routes._utils import get_sheet as _get_sheet


def _get_worksheet(sheet, tab_name):
    """Safely get a worksheet tab, returning None on failure."""
    if not sheet:
        return None
    try:
        return sheet.worksheet(tab_name)
    except Exception:
        return None


# ── Control Panel ──────────────────────────────────────────────
@capacity_bp.route("/panel")
@login_required
def panel():
    user = get_current_user()

    # Demo mode: show realistic fake status
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_LOBS, DEMO_EMPLOYEES
        api_status = {
            "legacy": {"ok": True, "status": 200},
            "new_api": {"ok": True, "status": 200},
        }
        status = {
            "forecast_rows": 2016,
            "req_rows": 2016,
            "emp_count": len(DEMO_EMPLOYEES),
            "forecast_updated": "2026-09-25 08:00",
            "req_updated": "2026-09-25 08:00",
            "emp_updated": "on file",
        }
        return render_template("capacity/panel.html",
            user=user,
            api_status=api_status,
            status=status,
            workloads=sorted(DEMO_LOBS),
            current_year=datetime.now(ZoneInfo(TIMEZONE)).year,
        )

    try:
        api_status = cp.test_connection()
    except Exception:
        api_status = {}

    # Ensure expected keys exist for the template
    if "legacy" not in api_status:
        api_status["legacy"] = {"ok": False, "status": 0}
    if "new_api" not in api_status:
        api_status["new_api"] = {"ok": False, "status": 0}

    # Sheet data status
    status = {
        "forecast_rows": 0, "req_rows": 0, "emp_count": 0,
        "forecast_updated": None, "req_updated": None, "emp_updated": None,
    }
    sheet = _get_sheet()
    if sheet:
        try:
            fc_ws = sheet.worksheet("FORECAST RAW")
            fc_all = fc_ws.get_all_values()
            status["forecast_rows"] = max(0, len(fc_all) - 3)
            if len(fc_all) > 4:
                status["forecast_updated"] = fc_all[3][1] if len(fc_all[3]) > 1 else None
        except Exception:
            pass
        try:
            rq_ws = sheet.worksheet("REQUIREMENTS RAW")
            rq_all = rq_ws.get_all_values()
            status["req_rows"] = max(0, len(rq_all) - 3)
            if len(rq_all) > 4:
                status["req_updated"] = rq_all[3][1] if len(rq_all[3]) > 1 else None
        except Exception:
            pass
        try:
            em_ws = sheet.worksheet("EMPLOYEES")
            em_all = em_ws.get_all_values()
            status["emp_count"] = max(0, len(em_all) - 1)
            if len(em_all) > 1:
                status["emp_updated"] = "on file"
        except Exception:
            pass

    # Employee count from Serevo's own roster (API pulls land here)
    try:
        from app.models import Employee
        db_count = Employee.query.filter(Employee.status != "Inactive").count()
        if db_count:
            status["emp_count"] = db_count
            latest = Employee.query.order_by(Employee.updated_at.desc()).first() if hasattr(Employee, "updated_at") else None
            status["emp_updated"] = latest.updated_at.strftime("%Y-%m-%d %H:%M") if latest and latest.updated_at else "in database"
    except Exception:
        pass

    return render_template("capacity/panel.html",
        user=user,
        api_status=api_status,
        status=status,
        workloads=sorted(cp.WORKLOADS.keys()),
        current_year=datetime.now(ZoneInfo(TIMEZONE)).year,
    )


# ── Test Connection endpoint ───────────────────────────────────
@capacity_bp.route("/test_connection")
@login_required
def test_connection():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify({
            "legacy": {"ok": True, "status": 200},
            "new_api": {"ok": True, "status": 200},
        })
    try:
        result = cp.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Refresh data from connected WFM system ─────────────────────
@capacity_bp.route("/refresh", methods=["POST"])
@login_required
def refresh():
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify({"success": True, "rows_written": 0,
                        "message": "Demo mode — data is pre-loaded",
                        "skipped": 0, "duration": "0s"})

    try:
        payload = request.get_json(silent=True) or {}
        pull_type = payload.get("type", "forecast")
        year = int(payload.get("year", datetime.now(ZoneInfo(TIMEZONE)).year))
        start_str = payload.get("start_date", f"{year}-01-01")
        end_str = payload.get("end_date", f"{year}-12-31")
        utc_offset = int(payload.get("utc_offset", -4))
        append = payload.get("append", True)

        start_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_str, "%Y-%m-%d").date()
        all_days = [(start_dt + timedelta(days=i))
                    for i in range((end_dt - start_dt).days + 1)]

        t0 = time.time()
        rows_written = 0
        skipped = 0

        # Employees: pull from the API straight into Serevo's roster (no sheet needed)
        if pull_type == "employees":
            try:
                employees = cp.fetch_employees()
            except requests.exceptions.Timeout:
                return jsonify({"success": False,
                                "error": "The connected system took too long to respond. Try again — if this persists, the API may be experiencing high load."})
            except requests.exceptions.ConnectionError:
                return jsonify({"success": False,
                                "error": "Could not reach the connected system — check your internet connection and API settings."})
            except Exception as e:
                log.exception("Employee pull error")
                return jsonify({"success": False,
                                "error": f"Employee pull failed: {e}"})
            if not employees:
                return jsonify({"success": False,
                                "error": "The connected system returned no employees — check the API connection in Settings → API Connections (Test must pass)."})
            created, updated = cp.upsert_employees_to_db(employees)
            sheet = _get_sheet()
            if sheet:
                try:
                    try:
                        em_ws = sheet.worksheet("EMPLOYEES")
                    except Exception:
                        em_ws = sheet.add_worksheet("EMPLOYEES", rows="1000", cols="25")
                    cp.write_employees_to_sheet(em_ws, employees)
                except Exception as e:
                    log.warning(f"Employee sheet write skipped: {e}")
            return jsonify({"success": True, "rows_written": created + updated,
                            "created": created, "updated": updated, "skipped": 0,
                            "duration": f"{int(time.time() - t0)}s",
                            "message": f"Headcount updated: {created} new, {updated} updated employees"})

        sheet = _get_sheet()
        if not sheet:
            return jsonify({"success": False, "error": "Could not open Google Sheet"})

        if pull_type in ("forecast", "all"):
            try:
                fc_ws = sheet.worksheet("FORECAST RAW")
            except Exception:
                fc_ws = sheet.add_worksheet("FORECAST RAW", rows="15000", cols="10")

            if not append:
                try:
                    fc_ws.clear()
                    fc_ws.update("A1", [["LOB", "Date", "Timestamp", "Offered", "AHT"]])
                except Exception:
                    pass

            for lob_name, wid in cp.WORKLOADS.items():
                for day in all_days:
                    intervals = cp.fetch_forecast_for_day(lob_name, wid, day, utc_offset)
                    if intervals:
                        written = cp.write_forecast_to_sheet(fc_ws, lob_name, intervals)
                        rows_written += written
                    else:
                        skipped += 1

        if pull_type in ("requirements", "all"):
            try:
                rq_ws = sheet.worksheet("REQUIREMENTS RAW")
            except Exception:
                rq_ws = sheet.add_worksheet("REQUIREMENTS RAW", rows="20000", cols="10")

            if not append:
                try:
                    rq_ws.clear()
                    rq_ws.update("A1", [["LOB", "Date", "Timestamp", "Agents Required"]])
                except Exception:
                    pass

            planning_units = cp.fetch_planning_units()
            for pu in planning_units:
                pu_id = pu.get("planning_unit_id") or pu.get("id", "")
                pu_name = pu.get("name", "")
                if not pu_id:
                    continue
                for day in all_days:
                    day_reqs = cp.fetch_requirements_for_day(pu_id, pu_name, day)
                    if day_reqs:
                        written = cp.write_requirements_to_sheet(rq_ws, day_reqs)
                        rows_written += written
                    else:
                        skipped += 1

        if pull_type in ("employees", "all"):
            try:
                em_ws = sheet.worksheet("EMPLOYEES")
            except Exception:
                em_ws = sheet.add_worksheet("EMPLOYEES", rows="1000", cols="25")

            employees = cp.fetch_employees()
            if employees:
                written = cp.write_employees_to_sheet(em_ws, employees)
                rows_written += written
            else:
                skipped += 1

        duration = f"{int(time.time() - t0)}s"
        return jsonify({"success": True, "rows_written": rows_written,
                        "skipped": skipped, "duration": duration})

    except Exception as e:
        log.exception("Capacity refresh error")
        return jsonify({"success": False, "error": str(e)})


# ── Single-day refresh (avoids Render 30s timeout) ────────────
@capacity_bp.route("/refresh/day", methods=["POST"])
@login_required
def refresh_day():
    """Pull forecast or requirements for ONE day only.
    The frontend loops day-by-day so each request finishes quickly."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify({"success": True, "rows_written": 0, "skipped": 0})

    try:
        payload = request.get_json(silent=True) or {}
        pull_type = payload.get("type", "forecast")
        day_str = payload.get("date")  # "YYYY-MM-DD"
        utc_offset = int(payload.get("utc_offset", -4))
        append = payload.get("append", True)

        if not day_str:
            return jsonify({"success": False, "error": "Missing 'date' parameter"})

        day_date = datetime.strptime(day_str, "%Y-%m-%d").date()
        rows_written = 0
        skipped = 0

        if pull_type == "forecast":
            sheet = _get_sheet()
            if not sheet:
                return jsonify({"success": False, "error": "Could not open Google Sheet"})
            try:
                fc_ws = sheet.worksheet("FORECAST RAW")
            except Exception:
                fc_ws = sheet.add_worksheet("FORECAST RAW", rows="15000", cols="10")
                fc_ws.update("A1", [["LOB", "Date", "Timestamp", "Offered", "AHT"]])

            for lob_name, wid in cp.WORKLOADS.items():
                intervals = cp.fetch_forecast_for_day(lob_name, wid, day_date, utc_offset)
                if intervals:
                    written = cp.write_forecast_to_sheet(fc_ws, lob_name, intervals)
                    rows_written += written
                else:
                    skipped += 1

        elif pull_type == "requirements":
            sheet = _get_sheet()
            if not sheet:
                return jsonify({"success": False, "error": "Could not open Google Sheet"})
            try:
                rq_ws = sheet.worksheet("REQUIREMENTS RAW")
            except Exception:
                rq_ws = sheet.add_worksheet("REQUIREMENTS RAW", rows="20000", cols="10")
                rq_ws.update("A1", [["LOB", "Date", "Timestamp", "Agents Required"]])

            planning_units = cp.fetch_planning_units()
            for pu in planning_units:
                pu_id = pu.get("planning_unit_id") or pu.get("id", "")
                pu_name = pu.get("name", "")
                if not pu_id:
                    continue
                day_reqs = cp.fetch_requirements_for_day(pu_id, pu_name, day_date)
                if day_reqs:
                    written = cp.write_requirements_to_sheet(rq_ws, day_reqs)
                    rows_written += written
                else:
                    skipped += 1

        return jsonify({"success": True, "rows_written": rows_written, "skipped": skipped})

    except Exception as e:
        log.exception(f"Day refresh error for {payload.get('date')}")
        return jsonify({"success": False, "error": str(e)})


# ── Capacity Plan View ─────────────────────────────────────────
def _build_plan(user, year, shrinkage, occupancy, answer_rate):
    """Compute the monthly capacity plan (list of {lob, months[...]}) for a year."""
    plan = []
    if True:
            if user and user.get("is_demo"):
                from app.demo_data import DEMO_LOBS, get_demo_forecast, get_demo_requirements, DEMO_EMPLOYEES
                import calendar, datetime as _dt

                # ── Which LOBs have language splits in the demo ─────
                # Check if any employee in that LOB speaks French
                demo_lang_lobs = set()
                for lob_name in DEMO_LOBS:
                    lob_emps = [e for e in DEMO_EMPLOYEES
                                if e.get("Latest Skill Name") == lob_name
                                and str(e.get("Status", "Active")).strip().lower() in ("active", "")]
                    for e in lob_emps:
                        langs = (e.get("Languages") or "English").lower()
                        if "french" in langs or "français" in langs:
                            demo_lang_lobs.add(lob_name)
                            break

                def _parse_demo_date(s):
                    s = str(s or "").strip()[:10]
                    if not s:
                        return None
                    try:
                        return _dt.datetime.strptime(s, "%Y-%m-%d").date()
                    except Exception:
                        return None

                def _demo_hc_for_month(lob_name, month_num, lang_filter=None):
                    """Count demo employees active in a given month, optionally by language."""
                    first_of_month = _dt.date(year, month_num, 1)
                    _, last_day = calendar.monthrange(year, month_num)
                    last_of_month = _dt.date(year, month_num, last_day)
                    count = 0
                    for e in DEMO_EMPLOYEES:
                        if e.get("Latest Skill Name") != lob_name:
                            continue
                        if str(e.get("Status", "Active")).strip().lower() not in ("active", ""):
                            continue
                        start = _parse_demo_date(e.get("Latest Skill Start"))
                        end = _parse_demo_date(e.get("End Date"))
                        if start and start > last_of_month:
                            continue
                        if end and end < first_of_month:
                            continue
                        if lang_filter:
                            langs = (e.get("Languages") or "English").lower()
                            if lang_filter == "EN" and "english" not in langs:
                                continue
                            if lang_filter == "FR" and "french" not in langs and "français" not in langs:
                                continue
                        count += 1
                    return count

                plan = []
                for lob_name in DEMO_LOBS:
                    # Determine variants: Combined + EN + FR if language split exists
                    if lob_name in demo_lang_lobs:
                        variants = [
                            (f"{lob_name} Combined", None),
                            (f"{lob_name} EN", "EN"),
                            (f"{lob_name} FR", "FR"),
                        ]
                    else:
                        variants = [(lob_name, None)]

                    for label, lang in variants:
                        lob_plan = {"lob": label, "months": []}
                        for month_num in range(1, 13):
                            sample_day = date(year, month_num, 15)
                            fc, _ = get_demo_forecast(lob_name, sample_day)
                            rq, _ = get_demo_requirements(lob_name, sample_day)
                            avg_vol = sum(r["offered"] for r in fc) / max(1, len(fc))
                            avg_req = sum(r["agents_required"] for r in rq) / max(1, len(rq))
                            peak_agents = max((r["agents_required"] for r in rq), default=0)
                            fte_req = round(avg_req / (1 - shrinkage), 1)
                            emp_count = _demo_hc_for_month(lob_name, month_num, lang)
                            gap = round(emp_count - fte_req, 1)
                            wd = sum(1 for d in range(1, calendar.monthrange(year, month_num)[1] + 1)
                                     if _dt.date(year, month_num, d).weekday() < 5)
                            lob_plan["months"].append({
                                "month":        month_num,
                                "month_label":  _dt.date(year, month_num, 1).strftime("%b-%y"),
                                "fc_offered":   round(avg_vol * len(fc)),
                                "fc_answered":  round(avg_vol * len(fc) * answer_rate),
                                "aht":          "—",
                                "psih_raw":     round(avg_req, 1),
                                "psih_shr":     round(avg_req / (1 - shrinkage), 1),
                                "fte_req":      fte_req,
                                "actual_hc":    emp_count,
                                "gap":          gap,
                                "occupancy":    occupancy,
                                "shrinkage":    shrinkage,
                                "working_days": wd,
                                "peak_agents":  round(peak_agents, 1),
                                "avg_agents":   round(avg_req, 1),
                            })
                        plan.append(lob_plan)
            else:
                # Try DB-backed plan first (from Serevo-generated forecasts)
                try:
                    plan = cp.build_capacity_plan_from_db(
                        year, shrinkage=shrinkage, occupancy=occupancy,
                        answer_rate=answer_rate)
                except Exception as e:
                    log.warning(f"DB capacity plan error: {e}")
                    plan = []

                # Fall back to Google Sheets if DB has no data
                if not plan:
                    sheet = _get_sheet()
                    fc_ws = _get_worksheet(sheet, "FORECAST RAW")
                    rq_ws = _get_worksheet(sheet, "REQUIREMENTS RAW")
                    em_ws = _get_worksheet(sheet, "EMPLOYEES")

                    plan = []
                    if fc_ws or rq_ws:
                        plan = cp.compute_capacity_plan(
                            fc_ws, rq_ws, em_ws, year,
                            shrinkage=shrinkage, occupancy=occupancy,
                            answer_rate=answer_rate)

    return plan


def _employee_headcount(user):
    """Headcount by LOB / planning unit: active, inactive, on-leave, total."""
    from collections import defaultdict
    if user and user.get("is_demo"):
        from app.demo_data import DEMO_EMPLOYEES
        emps = DEMO_EMPLOYEES
    else:
        try:
            from app.people.manager import get_employees
            emps, _ = get_employees(_get_sheet())
            emps = emps or []
        except Exception:
            emps = []
    by = defaultdict(lambda: {"active": 0, "inactive": 0, "on_leave": 0, "total": 0})
    for e in emps:
        pu = (e.get("Latest Skill Name") or e.get("LOB") or "Unassigned").strip() or "Unassigned"
        st = str(e.get("Status", "Active")).strip().lower()
        if st in ("loa", "leave", "on leave"):
            by[pu]["on_leave"] += 1
        elif st in ("inactive", "terminated", "deleted"):
            by[pu]["inactive"] += 1
        else:
            by[pu]["active"] += 1
        by[pu]["total"] += 1
    rows = []
    for pu, c in sorted(by.items()):
        rows.append({"planning_unit": pu, **c,
                     "loa_pct": round(c["on_leave"] / c["total"] * 100, 1) if c["total"] else 0})
    return rows


def _summary_rows(plan, months=None):
    """One row per LOB per month — the numeric capacity summary."""
    rows = []
    for lob in plan:
        for m in lob["months"]:
            if months and m["month"] not in months:
                continue
            rows.append({
                "lob": lob["lob"], "month": m["month"], "month_label": m["month_label"],
                "total_calls": m.get("fc_offered", 0), "answered": m.get("fc_answered", 0),
                "aht": m.get("aht", "—"), "psih_raw": m.get("psih_raw", 0), "psih_shr": m.get("psih_shr", 0),
                "fte_req": m.get("fte_req", 0), "actual_hc": m.get("actual_hc", 0), "gap": m.get("gap", 0),
                "peak_agents": m.get("peak_agents", 0), "avg_agents": m.get("avg_agents", 0),
                "working_days": m.get("working_days", 0),
            })
    return rows


@capacity_bp.route("/plan")
@login_required
def plan_view():
    user = get_current_user()
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        year = int(request.args.get("year", now.year))
        years = list(range(2024, now.year + 2))
        shrinkage = float(request.args.get("shrinkage", 30)) / 100.0
        occupancy = float(request.args.get("occupancy", 85)) / 100.0
        answer_rate = float(request.args.get("answer_rate", 92)) / 100.0
        sel_lobs = [l for l in request.args.getlist("lobs") if l]
        plan = _build_plan(user, year, shrinkage, occupancy, answer_rate)
        all_lobs = [p["lob"] for p in plan]
        if sel_lobs:
            plan = [p for p in plan if p["lob"] in sel_lobs]
        now_str = now.strftime("%Y-%m-%d %H:%M")
        return render_template("capacity/plan.html",
            user=user, plan=plan, year=year, years=years, now=now_str,
            all_lobs=all_lobs, sel_lobs=sel_lobs,
            shrinkage=round(shrinkage * 100), occupancy=round(occupancy * 100),
            answer_rate=round(answer_rate * 100))

    except Exception as e:
        log.exception("Capacity plan view error")
        return f"Capacity plan error: {str(e)}", 500


# ── Capacity Summary (numeric) + Headcount ─────────────────────
@capacity_bp.route("/")
@capacity_bp.route("/summary")
@login_required
def summary_view():
    user = get_current_user()
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        year = int(request.args.get("year", now.year))
        years = list(range(2024, now.year + 2))
        shrinkage = float(request.args.get("shrinkage", 30)) / 100.0
        occupancy = float(request.args.get("occupancy", 85)) / 100.0
        answer_rate = float(request.args.get("answer_rate", 92)) / 100.0
        sel_months = [int(m) for m in request.args.getlist("months") if m.isdigit()]
        sel_lobs = [l for l in request.args.getlist("lobs") if l]

        plan = _build_plan(user, year, shrinkage, occupancy, answer_rate)
        all_lobs = [p["lob"] for p in plan]
        if sel_lobs:
            plan = [p for p in plan if p["lob"] in sel_lobs]
        rows = _summary_rows(plan, sel_months or None)
        headcount = _employee_headcount(user)

        # Totals across selected rows
        tot = {"total_calls": sum(r["total_calls"] for r in rows),
               "fte_req": round(sum(r["fte_req"] for r in rows) / max(1, len(set(r["month"] for r in rows))), 1),
               "actual_hc": 0,
               "gap": 0}

        # Build per-LOB grouped data (metrics as rows, months as columns)
        lob_tables = []
        from collections import OrderedDict
        lob_order = []
        lob_map = OrderedDict()
        for r in rows:
            if r["lob"] not in lob_map:
                lob_map[r["lob"]] = {}
                lob_order.append(r["lob"])
            lob_map[r["lob"]][r["month"]] = r

        active_months = sorted(set(r["month"] for r in rows))
        month_labels = {m: date(year, m, 1).strftime("%b %Y") for m in active_months}

        # Compute per-LOB aggregate stats for summary
        total_fte = 0
        total_hc = 0
        lobs_in_deficit = 0
        monthly_fte = {}
        monthly_hc = {}

        for lob_name in lob_order:
            months_data = lob_map[lob_name]
            lob_entry = {"lob": lob_name, "months": months_data}
            lob_tables.append(lob_entry)

            # Aggregate: avg FTE, typical HC, deficit months
            deficit_count = 0
            for m, md in months_data.items():
                monthly_fte[m] = monthly_fte.get(m, 0) + md.get("fte_req", 0)
                monthly_hc[m] = monthly_hc.get(m, 0) + md.get("actual_hc", 0)
                if md.get("gap", 0) < 0:
                    deficit_count += 1
            if deficit_count > 0:
                lobs_in_deficit += 1

        # Summary totals
        if monthly_fte:
            total_fte = round(sum(monthly_fte.values()) / len(monthly_fte), 1)
            total_hc = round(sum(monthly_hc.values()) / len(monthly_hc))

        # Monthly gap stats for trend
        monthly_gaps = []
        for m in active_months:
            mg = monthly_hc.get(m, 0) - monthly_fte.get(m, 0)
            monthly_gaps.append({"month": m, "label": month_labels[m], "gap": round(mg, 1),
                                 "fte": round(monthly_fte.get(m, 0), 1),
                                 "hc": monthly_hc.get(m, 0)})

        avg_gap = round(sum(g["gap"] for g in monthly_gaps) / max(1, len(monthly_gaps)), 1) if monthly_gaps else 0
        best_gap = max((g["gap"] for g in monthly_gaps), default=0)
        worst_gap = min((g["gap"] for g in monthly_gaps), default=0)

        tot["fte_req"] = total_fte
        tot["actual_hc"] = total_hc
        tot["gap"] = round(total_hc - total_fte, 1)

        return render_template("capacity/summary.html", user=user, rows=rows, totals=tot,
                               headcount=headcount, year=year, years=years,
                               all_lobs=all_lobs, sel_lobs=sel_lobs, sel_months=sel_months,
                               shrinkage_pct=int(shrinkage * 100), occupancy_pct=int(occupancy * 100),
                               answer_rate_pct=int(answer_rate * 100),
                               now=now.strftime("%Y-%m-%d %H:%M"),
                               month_names=[(m, date(year, m, 1).strftime("%b")) for m in range(1, 13)],
                               lob_tables=lob_tables, active_months=active_months,
                               month_labels=month_labels, monthly_gaps=monthly_gaps,
                               avg_gap=avg_gap, best_gap=best_gap, worst_gap=worst_gap,
                               lobs_in_deficit=lobs_in_deficit)
    except Exception as e:
        log.exception("Capacity summary error")
        return f"Capacity summary error: {str(e)}", 500




@capacity_bp.route("/summary/export")
@login_required
def summary_export():
    """CSV export of the summary rows (opens in Excel)."""
    import csv, io
    from flask import Response
    user = get_current_user()
    now = datetime.now(ZoneInfo(TIMEZONE))
    year = int(request.args.get("year", now.year))
    shrinkage = float(request.args.get("shrinkage", 30)) / 100.0
    occupancy = float(request.args.get("occupancy", 85)) / 100.0
    answer_rate = float(request.args.get("answer_rate", 92)) / 100.0
    sel_months = [int(m) for m in request.args.getlist("months") if m.isdigit()]
    sel_lobs = [l for l in request.args.getlist("lobs") if l]
    plan = _build_plan(user, year, shrinkage, occupancy, answer_rate)
    if sel_lobs:
        plan = [p for p in plan if p["lob"] in sel_lobs]
    rows = _summary_rows(plan, sel_months or None)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["LOB", "Month", "Forecast Calls", "Forecast Answered", "AHT (s)", "PSIH (no shrink)",
                f"PSIH ({int(shrinkage*100)}% shrink)", "FTE Required", "Actual HC", "Gap (HC - FTE)",
                "Peak Agents", "Avg Agents", "Working Days"])
    for r in rows:
        w.writerow([r["lob"], r["month_label"], r["total_calls"], r["answered"], r["aht"], r["psih_raw"],
                    r["psih_shr"], r["fte_req"], r["actual_hc"], r["gap"], r["peak_agents"],
                    r["avg_agents"], r["working_days"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=capacity_summary_{year}.csv"})


# ══════════════════════════════════════════════════════════════
# DATA SOURCES — manage multi-source historical data ingestion
# ══════════════════════════════════════════════════════════════

@capacity_bp.route("/sources")
@login_required
def sources_view():
    """List all data sources with sync status."""
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return redirect(url_for("capacity.panel"))

    from app.models import DataSource, APIConnection
    from app.ingestion.service import get_historical_stats

    sources = DataSource.query.order_by(DataSource.created_at.desc()).all()
    api_connections = APIConnection.query.filter_by(is_active=True).all()
    stats = get_historical_stats()

    return render_template("capacity/sources.html",
        user=user, sources=sources,
        api_connections=api_connections, stats=stats)


@capacity_bp.route("/sources/add", methods=["POST"])
@login_required
def sources_add():
    """Create a new data source."""
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    from app.models import DataSource
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    source_type = data.get("source_type", "google_sheet")
    if not name:
        return jsonify({"ok": False, "error": "Name is required"})

    src = DataSource(
        name=name,
        source_type=source_type,
        sheet_key=data.get("sheet_key", "").strip(),
        tab_name=data.get("tab_name", "").strip(),
        service_account_json=data.get("service_account_json", "").strip(),
        api_connection_id=data.get("api_connection_id") or None,
        api_endpoint=data.get("api_endpoint", "").strip(),
        column_mapping=data.get("column_mapping") or {},
        is_active=True,
    )
    from app.models import db
    db.session.add(src)
    db.session.commit()
    return jsonify({"ok": True, "id": src.id, "message": f"Source '{name}' created"})


@capacity_bp.route("/sources/<int:source_id>", methods=["PUT"])
@login_required
def sources_update(source_id):
    """Update an existing data source."""
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    from app.models import DataSource, db
    src = DataSource.query.get_or_404(source_id)
    data = request.get_json(silent=True) or {}

    for field in ("name", "source_type", "sheet_key", "tab_name",
                  "service_account_json", "api_endpoint", "is_active"):
        if field in data:
            setattr(src, field, data[field])
    if "api_connection_id" in data:
        src.api_connection_id = data["api_connection_id"] or None
    if "column_mapping" in data:
        src.column_mapping = data["column_mapping"]

    db.session.commit()
    return jsonify({"ok": True, "message": f"Source '{src.name}' updated"})


@capacity_bp.route("/sources/<int:source_id>", methods=["DELETE"])
@login_required
def sources_delete(source_id):
    """Delete a data source (doesn't delete the ingested data)."""
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    from app.models import DataSource, db
    src = DataSource.query.get_or_404(source_id)
    name = src.name
    db.session.delete(src)
    db.session.commit()
    return jsonify({"ok": True, "message": f"Source '{name}' deleted"})


@capacity_bp.route("/sources/<int:source_id>/sync", methods=["POST"])
@login_required
def sources_sync(source_id):
    """Sync one data source now."""
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    from app.ingestion.service import sync_source
    result = sync_source(source_id)
    return jsonify(result)


@capacity_bp.route("/sources/sync-all", methods=["POST"])
@login_required
def sources_sync_all():
    """Sync all active data sources."""
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not user or not user.get("is_admin"):
        return jsonify({"ok": False, "error": "Admin only"}), 403

    from app.ingestion.service import sync_all_sources
    results = sync_all_sources()
    return jsonify({"ok": True, "results": results})
