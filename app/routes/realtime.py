"""
routes/realtime.py — Real-Time Monitoring blueprint
====================================================
Live queue monitoring, adherence, and staffing alerts.
"""

import logging
import datetime

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, get_current_user

log = logging.getLogger("serevo.realtime")

realtime_bp = Blueprint("realtime", __name__, url_prefix="/realtime")


def _get_sheet():
    try:
        from app.data_source import _open_capacity_sheet
        sheet, err = _open_capacity_sheet()
        return None if err else sheet
    except Exception:
        return None


# ── Main view ───────────────────────────────────────────────
@realtime_bp.route("/")
@login_required
def index():
    user = get_current_user()
    from app.realtime.engine import get_available_lobs
    sheet = _get_sheet()
    lobs = get_available_lobs(sheet)
    return render_template("realtime/index.html", user=user, lobs=lobs)


# ── Intraday snapshot (API) ────────────────────────────────
@realtime_bp.route("/snapshot", methods=["POST"])
@login_required
def snapshot():
    """
    POST JSON: {lob, date?}
    Returns full intraday snapshot with intervals, alerts, summary.
    """
    from app.realtime.engine import get_intraday_snapshot

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())
        sheet = _get_sheet()

        result = get_intraday_snapshot(lob, date_obj, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Snapshot error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Adherence (API) ────────────────────────────────────────
@realtime_bp.route("/adherence", methods=["POST"])
@login_required
def adherence():
    """
    POST JSON: {lob, date?}
    Returns per-agent adherence data.
    """
    from app.realtime.engine import get_adherence_snapshot

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())
        sheet = _get_sheet()

        result = get_adherence_snapshot(lob, date_obj, sheet)
        result["success"] = True
        return jsonify(result)

    except Exception as e:
        log.error(f"Adherence error: {e}")
        return jsonify({"success": False, "error": str(e)})


# ── Service level tracker (API) ────────────────────────────
@realtime_bp.route("/service-level", methods=["POST"])
@login_required
def service_level():
    """
    POST JSON: {lob, date?}
    Returns estimated SL per interval.
    """
    from app.realtime.engine import get_service_level_intraday

    try:
        payload = request.get_json(silent=True) or {}
        lob = payload.get("lob", "").strip()
        date_str = payload.get("date", "")

        if not lob:
            return jsonify({"success": False, "error": "LOB is required"})

        date_obj = (datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    if date_str else datetime.date.today())
        sheet = _get_sheet()

        result = get_service_level_intraday(lob, date_obj, sheet)
        return jsonify({"success": True, "intervals": result})

    except Exception as e:
        log.error(f"SL tracker error: {e}")
        return jsonify({"success": False, "error": str(e)})
