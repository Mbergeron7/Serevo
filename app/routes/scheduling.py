"""
routes/scheduling.py — Scheduling blueprint
=============================================
Schedule generation, shift assignments, and coverage analysis.
"""

import logging
import datetime

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

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
