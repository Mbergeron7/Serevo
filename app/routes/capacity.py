"""
routes/capacity.py — Capacity Planning blueprint
=================================================
Control panel, data refresh endpoints, and capacity plan view.
"""

import time
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify)
from app.auth import login_required, get_current_user
from app.capacity import planning as cp
from config import cfg

log = logging.getLogger("serevo.capacity")

capacity_bp = Blueprint("capacity", __name__, url_prefix="/capacity")

TIMEZONE = cp.TIMEZONE


# ── helpers to get Google Sheet worksheets ─────────────────────
def _get_sheet():
    """Return the main capacity Google Sheet object, or None."""
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        if err:
            log.warning(f"Sheet open error: {err}")
            return None
        return sheet
    except Exception as e:
        log.warning(f"Sheet import error: {e}")
        return None


def _get_worksheet(sheet, tab_name):
    """Safely get a worksheet tab, returning None on failure."""
    if not sheet:
        return None
    try:
        return sheet.worksheet(tab_name)
    except Exception:
        return None


# ── Control Panel ──────────────────────────────────────────────
@capacity_bp.route("/")
@login_required
def panel():
    user = get_current_user()
    try:
        api_status = cp.test_connection()
    except Exception:
        api_status = {
            "ok": False,
            "legacy": {"ok": False, "status": 0},
            "new_api": {"ok": False, "status": 0},
        }

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
    try:
        result = cp.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Refresh data from PeopleWare ───────────────────────────────
@capacity_bp.route("/refresh", methods=["POST"])
@login_required
def refresh():
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

        sheet = _get_sheet()
        if not sheet:
            return jsonify({"success": False, "error": "Could not open Google Sheet"})

        t0 = time.time()
        rows_written = 0
        skipped = 0

        if pull_type in ("forecast", "all"):
            try:
                fc_ws = sheet.worksheet("FORECAST RAW")
            except Exception:
                fc_ws = sheet.add_worksheet("FORECAST RAW", rows="15000", cols="50")
                fc_ws.update("A1", [["FORECAST RAW — Offered Calls by Workload (30-min intervals)"]])
                fc_ws.update("B3", [["Timestamp"]])

            if not append:
                try:
                    all_vals = fc_ws.get_all_values()
                    if len(all_vals) > 3:
                        fc_ws.delete_rows(4, len(all_vals))
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
                rq_ws = sheet.add_worksheet("REQUIREMENTS RAW", rows="20000", cols="60")
                rq_ws.update("A1", [["REQUIREMENTS RAW — Agent Requirements (30-min intervals)"]])
                rq_ws.update("B3", [["Timestamp"]])

            if not append:
                try:
                    all_vals = rq_ws.get_all_values()
                    if len(all_vals) > 3:
                        rq_ws.delete_rows(4, len(all_vals))
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


# ── Capacity Plan View ─────────────────────────────────────────
@capacity_bp.route("/plan")
@login_required
def plan_view():
    user = get_current_user()
    try:
        now = datetime.now(ZoneInfo(TIMEZONE))
        year = int(request.args.get("year", now.year))
        years = list(range(2024, now.year + 2))

        sheet = _get_sheet()
        fc_ws = _get_worksheet(sheet, "FORECAST RAW")
        rq_ws = _get_worksheet(sheet, "REQUIREMENTS RAW")
        em_ws = _get_worksheet(sheet, "EMPLOYEES")

        plan = []
        if fc_ws or rq_ws:
            plan = cp.compute_capacity_plan(fc_ws, rq_ws, em_ws, year)

        now_str = now.strftime("%Y-%m-%d %H:%M")
        return render_template("capacity/plan.html",
            user=user, plan=plan, year=year, years=years, now=now_str)

    except Exception as e:
        log.exception("Capacity plan view error")
        return f"Capacity plan error: {str(e)}", 500
