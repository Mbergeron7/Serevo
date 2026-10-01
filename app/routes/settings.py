"""
routes/settings.py — API Connection Manager (Phase 1.3)
========================================================
Admin page for managing external API connections to workforce
management platforms. Supports adding, editing, testing, and
removing API credentials.
"""

import json
import logging
from datetime import datetime

from flask import (Blueprint, render_template, request, jsonify)
from app.auth import login_required, admin_required, get_current_user
from config import cfg

log = logging.getLogger("serevo.settings")

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

# Supported provider templates
PROVIDERS = {
    "generic": {
        "label": "Generic REST API",
        "auth_types": ["bearer", "basic", "api_key"],
        "fields": ["base_url"],
    },
    "nice": {
        "label": "NICE CXone",
        "auth_types": ["bearer", "oauth2"],
        "fields": ["base_url", "client_id", "client_secret"],
    },
    "genesys": {
        "label": "Genesys Cloud",
        "auth_types": ["oauth2"],
        "fields": ["base_url", "client_id", "client_secret", "org_id"],
    },
    "five9": {
        "label": "Five9",
        "auth_types": ["basic"],
        "fields": ["base_url"],
    },
    "avaya": {
        "label": "Avaya",
        "auth_types": ["bearer", "basic"],
        "fields": ["base_url"],
    },
    "talkdesk": {
        "label": "Talkdesk",
        "auth_types": ["bearer", "oauth2"],
        "fields": ["base_url", "client_id", "client_secret"],
    },
    "wfm_legacy": {
        "label": "WFM Platform (Legacy API)",
        "auth_types": ["bearer", "api_key"],
        "fields": ["base_url", "api_key"],
    },
}


@settings_bp.route("/")
@admin_required
def index():
    from app.models import AppSetting
    sheets_key = AppSetting.get("google_sheet_key", "")
    sheets_status = AppSetting.get("google_sheets_status", "not configured")
    return render_template("settings/index.html",
        user=get_current_user(),
        sheets_key=sheets_key,
        sheets_status=sheets_status,
    )


@settings_bp.route("/google-sheets")
@admin_required
def google_sheets():
    from app.models import AppSetting
    sheets_key = AppSetting.get("google_sheet_key", "")
    has_creds = bool(AppSetting.get("google_service_account_json", ""))
    sheets_status = AppSetting.get("google_sheets_status", "not configured")
    return render_template("settings/google_sheets.html",
        user=get_current_user(),
        sheets_key=sheets_key,
        has_creds=has_creds,
        sheets_status=sheets_status,
    )


@settings_bp.route("/google-sheets/save", methods=["POST"])
@admin_required
def save_google_sheets():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AppSetting
    data = request.get_json(silent=True) or {}
    sheet_key = data.get("sheet_key", "").strip()
    sa_json = data.get("service_account_json", "").strip()

    if not sheet_key:
        return jsonify({"success": False, "error": "Sheet key is required"})

    # Validate the JSON if provided
    if sa_json:
        try:
            import json as json_mod
            parsed = json_mod.loads(sa_json)
            if "client_email" not in parsed:
                return jsonify({"success": False, "error": "Invalid service account JSON — missing client_email"})
        except (json.JSONDecodeError, ValueError):
            return jsonify({"success": False, "error": "Invalid JSON format"})

    AppSetting.set("google_sheet_key", sheet_key)
    if sa_json:
        AppSetting.set("google_service_account_json", sa_json)
    AppSetting.set("google_sheets_status", "configured")
    db.session.commit()

    # Reset the cached data source so it picks up new config
    import app.data_source as ds
    ds._INSTANCE = None

    return jsonify({"success": True})


def _friendly_sheet_error(e):
    log.warning("Google Sheets error (%s): %s", type(e).__name__, e)
    cls = type(e).__name__.lower()
    msg = str(e)
    low = msg.lower()
    # gspread raises SpreadsheetNotFound with an empty message for permission / not-shared errors
    if "spreadsheetnotfound" in cls or (not msg and "notfound" in cls):
        return ("The sheet could not be found. Make sure the sheet key is correct AND the sheet "
                "is shared with the service account email (client_email in your JSON key file).")
    if "403" in low or "permission" in low or "does not have permission" in low:
        return ("Google refused access (403). Share the sheet with the service account email "
                "(client_email in the JSON) as Viewer, and make sure the Google Sheets API and "
                "Google Drive API are enabled in the Google Cloud project.")
    if "404" in low or "not found" in low:
        return "Sheet not found (404). Check the Google Sheet key — it's the long ID between /d/ and /edit in the sheet's URL."
    if "invalid_grant" in low or "jwt" in low or "private key" in low or "pem" in low:
        return "The service account JSON was rejected by Google. Re-download the key file and paste the entire contents."
    if "expecting value" in low or "jsondecode" in low:
        return "The service account box does not contain valid JSON — paste the whole file, starting with { and ending with }."
    if not msg:
        return ("Could not connect to Google Sheets. Make sure the sheet is shared with the "
                "service account email and both the Google Sheets API and Google Drive API are "
                "enabled in the Google Cloud project.")
    return f"Could not open the sheet: {msg}"


@settings_bp.route("/google-sheets/test", methods=["POST"])
@admin_required
def test_google_sheets():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AppSetting
    from app.data_source import _open_capacity_sheet

    # Test whatever is in the form (unsaved), falling back to the saved config
    data = request.get_json(silent=True) or {}
    form_key = (data.get("sheet_key") or "").strip()
    form_json = (data.get("service_account_json") or "").strip()
    if form_key or form_json:
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            key = form_key or AppSetting.get("google_sheet_key", "")
            sa = form_json or AppSetting.get("google_service_account_json", "")
            if not key:
                return jsonify({"success": False, "error": "Enter the Google Sheet key first"})
            if not sa:
                return jsonify({"success": False, "error": "Paste the service account JSON first"})
            creds = ServiceAccountCredentials.from_json_keyfile_dict(
                json.loads(sa), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
            sheet = gspread.authorize(creds).open_by_key(key)
            err = None
        except Exception as e:
            sheet, err = None, _friendly_sheet_error(e)
    else:
        sheet, err = _open_capacity_sheet()
    if err:
        AppSetting.set("google_sheets_status", "error")
        db.session.commit()
        return jsonify({"success": False, "error": err})

    # Try reading the sheet title as a basic test
    try:
        title = sheet.title
        tabs = [ws.title for ws in sheet.worksheets()]
        AppSetting.set("google_sheets_status", "connected")
        db.session.commit()
        return jsonify({"success": True, "title": title, "tabs": tabs})
    except Exception as e:
        AppSetting.set("google_sheets_status", "error")
        db.session.commit()
        return jsonify({"success": False, "error": str(e)})


@settings_bp.route("/google-sheets/disconnect", methods=["POST"])
@admin_required
def disconnect_google_sheets():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AppSetting
    AppSetting.set("google_sheet_key", "")
    AppSetting.set("google_service_account_json", "")
    AppSetting.set("google_sheets_status", "not configured")
    db.session.commit()

    import app.data_source as ds
    ds._INSTANCE = None

    return jsonify({"success": True})


@settings_bp.route("/api-connections")
@admin_required
def api_connections():
    from app.models import APIConnection
    connections = APIConnection.query.order_by(APIConnection.created_at.desc()).all()
    return render_template("settings/api_connections.html",
        user=get_current_user(),
        connections=connections,
        providers=PROVIDERS,
    )


@settings_bp.route("/api-connections/save", methods=["POST"])
@admin_required
def save_connection():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, APIConnection
    data = request.get_json(silent=True) or {}

    conn_id   = data.get("id")
    name      = data.get("name", "").strip()
    provider  = data.get("provider", "generic")
    base_url  = data.get("base_url", "").strip()
    auth_type = data.get("auth_type", "bearer")

    if not name:
        return jsonify({"success": False, "error": "Connection name is required"})

    # Build credentials dict (never store raw — in production, encrypt this)
    creds = {}
    for key in ["api_key", "username", "password", "client_id", "client_secret",
                 "access_token", "org_id"]:
        val = data.get(key, "").strip()
        if val:
            creds[key] = val

    if conn_id:
        conn = APIConnection.query.get(conn_id)
        if not conn:
            return jsonify({"success": False, "error": "Connection not found"})
        conn.name = name
        conn.provider = provider
        conn.base_url = base_url
        conn.auth_type = auth_type
        if creds:  # only update credentials if provided
            conn.credentials = json.dumps(creds)
    else:
        conn = APIConnection(
            name=name,
            provider=provider,
            base_url=base_url,
            auth_type=auth_type,
            credentials=json.dumps(creds),
        )
        db.session.add(conn)

    db.session.commit()
    return jsonify({"success": True, "id": conn.id})


def _clean_token(raw):
    """Strip whitespace/newlines from a stored token. Some tokens look
    base64-encoded but must be used as-is (not decoded)."""
    return (raw or "").strip().replace("\n", "").replace("\r", "")


@settings_bp.route("/api-connections/test", methods=["POST"])
@admin_required
def test_connection():
    """Test an API connection using the registered connector (or generic fallback)."""
    dg = _demo_guard()
    if dg:
        return dg
    from app.connectors.sync import test_connection as connector_test

    data = request.get_json(silent=True) or {}
    conn_id = data.get("id")
    if not conn_id:
        return jsonify({"success": False, "error": "No connection ID"})

    success, message = connector_test(conn_id)
    return jsonify({"success": success, "status": "ok" if success else "error",
                    "message": message})


@settings_bp.route("/api-connections/sync", methods=["POST"])
@admin_required
def sync_connection():
    """Sync data from an API connection using the connector framework."""
    dg = _demo_guard()
    if dg:
        return dg
    from app.connectors.sync import sync_employees, sync_forecasts, sync_schedules

    data = request.get_json(silent=True) or {}
    conn_id = data.get("id")
    sync_type = data.get("type", "employees")  # employees | forecasts | schedules
    if not conn_id:
        return jsonify({"ok": False, "error": "No connection ID"})

    if sync_type == "employees":
        result = sync_employees(conn_id)
    elif sync_type == "forecasts":
        date_from = data.get("date_from", datetime.utcnow().strftime("%Y-%m-%d"))
        date_to = data.get("date_to", datetime.utcnow().strftime("%Y-%m-%d"))
        workload_ids = data.get("workload_ids")
        result = sync_forecasts(conn_id, date_from, date_to, workload_ids)
    elif sync_type == "schedules":
        date_from = data.get("date_from", datetime.utcnow().strftime("%Y-%m-%d"))
        date_to = data.get("date_to", datetime.utcnow().strftime("%Y-%m-%d"))
        employee_ids = data.get("employee_ids")
        result = sync_schedules(conn_id, date_from, date_to, employee_ids)
    else:
        return jsonify({"ok": False, "error": f"Unknown sync type: {sync_type}"})

    return jsonify(result)


@settings_bp.route("/api-connections/delete", methods=["POST"])
@admin_required
def delete_connection():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, APIConnection
    data = request.get_json(silent=True) or {}
    conn_id = data.get("id")
    if not conn_id:
        return jsonify({"success": False, "error": "No connection ID"})
    conn = APIConnection.query.get(conn_id)
    if not conn:
        return jsonify({"success": False, "error": "Connection not found"})
    db.session.delete(conn)
    db.session.commit()
    return jsonify({"success": True})


@settings_bp.route("/api-connections/toggle", methods=["POST"])
@admin_required
def toggle_connection():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, APIConnection
    data = request.get_json(silent=True) or {}
    conn_id = data.get("id")
    conn = APIConnection.query.get(conn_id)
    if not conn:
        return jsonify({"success": False, "error": "Connection not found"})
    conn.is_active = not conn.is_active
    db.session.commit()
    return jsonify({"success": True, "is_active": conn.is_active})


# ═══════════════════════════════════════════════════════════════
# USER MANAGEMENT (Phase 2.3)
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/users")
@admin_required
def users():
    from app.models import User
    all_users = User.query.order_by(User.created_at.desc()).all()
    return render_template("settings/users.html",
        user=get_current_user(),
        users=all_users,
    )


@settings_bp.route("/users/save", methods=["POST"])
@admin_required
def save_user():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, User
    from flask_bcrypt import generate_password_hash

    data = request.get_json(silent=True) or {}
    user_id = data.get("id")
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    role = data.get("role", "viewer")
    password = data.get("password", "")
    is_demo = bool(data.get("is_demo", False))

    if not email:
        return jsonify({"success": False, "error": "Email is required"})
    if role not in ("viewer", "admin"):
        role = "viewer"

    if user_id:
        # Edit existing
        user = User.query.get(user_id)
        if not user:
            return jsonify({"success": False, "error": "User not found"})
        user.display_name = name or user.display_name
        user.role = role
        if password:
            if len(password) < 6:
                return jsonify({"success": False, "error": "Password must be at least 6 characters"})
            user.password_hash = generate_password_hash(password).decode("utf-8")
        db.session.commit()
        # Set is_demo via raw SQL to avoid ORM column-missing issues
        try:
            db.session.execute(
                db.text("UPDATE users SET is_demo = :val WHERE id = :uid"),
                {"val": is_demo, "uid": user.id},
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
    else:
        # New user
        if User.query.filter_by(email=email).first():
            return jsonify({"success": False, "error": "A user with that email already exists"})
        if not password or len(password) < 6:
            return jsonify({"success": False, "error": "Password must be at least 6 characters"})
        user = User(
            email=email,
            password_hash=generate_password_hash(password).decode("utf-8"),
            display_name=name or email.split("@")[0].title(),
            role=role,
        )
        db.session.add(user)
        db.session.commit()
        # Set is_demo via raw SQL
        try:
            db.session.execute(
                db.text("UPDATE users SET is_demo = :val WHERE id = :uid"),
                {"val": is_demo, "uid": user.id},
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify({"success": True, "id": user.id})


@settings_bp.route("/users/toggle", methods=["POST"])
@admin_required
def toggle_user():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, User
    data = request.get_json(silent=True) or {}
    user_id = data.get("id")
    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "error": "User not found"})
    # Don't let admin deactivate themselves
    current = get_current_user()
    if current and current["id"] == user.id:
        return jsonify({"success": False, "error": "You can't deactivate your own account"})
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({"success": True, "is_active": user.is_active})


# ═══════════════════════════════════════════════════════════════
# CUSTOMIZATION (Admin-only)
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/customization")
@admin_required
def customization():
    """Main customization page — all admin-configurable settings."""
    user = get_current_user()

    # Demo mode — return mock data instead of querying the DB
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_settings_data
        demo = get_demo_settings_data()
        return render_template("settings/customization.html", user=user, **demo)

    from app.models import (SegmentCode, ShiftTemplate, LOBSetting,
                            PlanningUnit, TimeOffType, OvertimeRule, ScheduleRule,
                            Holiday, SkillGroup, AdherenceException, AlertConfig,
                            BrandSetting, LobMapping)

    segments = [s.to_dict() for s in SegmentCode.query.order_by(SegmentCode.sort_order, SegmentCode.label).all()]
    shifts = [s.to_dict() for s in ShiftTemplate.query.order_by(ShiftTemplate.sort_order, ShiftTemplate.name).all()]
    lob_settings = [s.to_dict() for s in LOBSetting.query.all()]
    all_lobs = [{"id": pu.id, "name": pu.name} for pu in PlanningUnit.query.order_by(PlanningUnit.name).all()]

    # New customization data
    time_off_types = [t.to_dict() for t in TimeOffType.query.order_by(TimeOffType.sort_order).all()]
    ot_rules = [r.to_dict() for r in OvertimeRule.query.order_by(OvertimeRule.name).all()]
    sched_rules = [r.to_dict() for r in ScheduleRule.query.order_by(ScheduleRule.name).all()]
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    cur_year = dt.now(ZoneInfo("US/Eastern")).year
    holidays = [h.to_dict() for h in Holiday.query.filter_by(year=cur_year).order_by(Holiday.date).all()]
    skill_groups = [g.to_dict() for g in SkillGroup.query.order_by(SkillGroup.name).all()]
    adherence_codes = [a.to_dict() for a in AdherenceException.query.order_by(AdherenceException.sort_order).all()]
    alerts = [a.to_dict() for a in AlertConfig.query.order_by(AlertConfig.name).all()]
    brand = BrandSetting.query.first()
    brand_data = brand.to_dict() if brand else {}
    lob_mappings = [{"id": m.id, "source_name": m.source_name,
                     "planning_unit_name": m.planning_unit_name}
                    for m in LobMapping.query.order_by(LobMapping.source_name).all()]

    return render_template("settings/customization.html",
        user=user,
        segments=segments, shifts=shifts,
        lob_settings=lob_settings, all_lobs=all_lobs,
        time_off_types=time_off_types, ot_rules=ot_rules,
        sched_rules=sched_rules, holidays=holidays,
        skill_groups=skill_groups, adherence_codes=adherence_codes,
        alerts=alerts, brand_data=brand_data, current_year=cur_year,
        lob_mappings=lob_mappings,
    )


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")
    return None


# ── Segment Codes CRUD ───────────────────────────────────────

@settings_bp.route("/customization/segments/save", methods=["POST"])
@admin_required
def save_segment():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SegmentCode
    data = request.get_json(silent=True) or {}
    seg_id = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if seg_id:
        seg = SegmentCode.query.get(seg_id)
        if not seg:
            return jsonify({"success": False, "error": "Segment not found"})
        seg.code = code
        seg.label = label
        seg.color = data.get("color", seg.color)
        seg.is_productive = bool(data.get("is_productive", seg.is_productive))
        seg.is_paid = bool(data.get("is_paid", seg.is_paid))
        seg.sort_order = int(data.get("sort_order", seg.sort_order))
        seg.offset_mins = data.get("offset_mins") if data.get("offset_mins") is not None else seg.offset_mins
        seg.duration_mins = data.get("duration_mins") if data.get("duration_mins") is not None else seg.duration_mins
        seg.is_flexible = bool(data.get("is_flexible", seg.is_flexible))
        seg.window_start_mins = data.get("window_start_mins") if data.get("window_start_mins") is not None else seg.window_start_mins
        seg.window_end_mins = data.get("window_end_mins") if data.get("window_end_mins") is not None else seg.window_end_mins
    else:
        if SegmentCode.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        seg = SegmentCode(
            code=code, label=label,
            color=data.get("color", "#6b7280"),
            is_productive=bool(data.get("is_productive", True)),
            is_paid=bool(data.get("is_paid", True)),
            sort_order=int(data.get("sort_order", 0)),
            offset_mins=data.get("offset_mins"),
            duration_mins=data.get("duration_mins"),
            is_flexible=bool(data.get("is_flexible", False)),
            window_start_mins=data.get("window_start_mins"),
            window_end_mins=data.get("window_end_mins"),
        )
        db.session.add(seg)
    db.session.commit()
    return jsonify({"success": True, "segment": seg.to_dict()})


@settings_bp.route("/customization/segments/delete", methods=["POST"])
@admin_required
def delete_segment():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SegmentCode
    data = request.get_json(silent=True) or {}
    seg = SegmentCode.query.get(data.get("id"))
    if not seg:
        return jsonify({"success": False, "error": "Segment not found"})
    if seg.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default segment code"})
    db.session.delete(seg)
    db.session.commit()
    return jsonify({"success": True})


# ── Shift Templates CRUD ─────────────────────────────────────

@settings_bp.route("/customization/shifts/save", methods=["POST"])
@admin_required
def save_shift():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ShiftTemplate
    data = request.get_json(silent=True) or {}
    shift_id = data.get("id")
    name = (data.get("name") or "").strip()
    start_time = (data.get("start_time") or "").strip()
    end_time = (data.get("end_time") or "").strip()
    if not name or not start_time or not end_time:
        return jsonify({"success": False, "error": "Name, start time, and end time are required"})

    segments_json = json.dumps(data.get("segments", []))

    pu_id = data.get("planning_unit_id") or None
    if pu_id:
        pu_id = int(pu_id)
    day_type = data.get("day_type", "any") or "any"
    shift_category = data.get("shift_category", "any") or "any"

    if shift_id:
        shift = ShiftTemplate.query.get(shift_id)
        if not shift:
            return jsonify({"success": False, "error": "Shift template not found"})
        shift.name = name
        shift.start_time = start_time
        shift.end_time = end_time
        shift.hours = float(data.get("hours", shift.hours))
        shift.shift_type = data.get("shift_type", shift.shift_type)
        shift.segments_json = segments_json
        shift.sort_order = int(data.get("sort_order", shift.sort_order))
        shift.planning_unit_id = pu_id
        shift.day_type = day_type
        shift.shift_category = shift_category
    else:
        shift = ShiftTemplate(
            name=name, start_time=start_time, end_time=end_time,
            hours=float(data.get("hours", 8.0)),
            shift_type=data.get("shift_type", "full"),
            segments_json=segments_json,
            sort_order=int(data.get("sort_order", 0)),
            planning_unit_id=pu_id,
            day_type=day_type,
            shift_category=shift_category,
        )
        db.session.add(shift)
    db.session.commit()
    return jsonify({"success": True, "shift": shift.to_dict()})


@settings_bp.route("/customization/shifts/delete", methods=["POST"])
@admin_required
def delete_shift():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ShiftTemplate
    data = request.get_json(silent=True) or {}
    shift = ShiftTemplate.query.get(data.get("id"))
    if not shift:
        return jsonify({"success": False, "error": "Shift template not found"})
    db.session.delete(shift)
    db.session.commit()
    return jsonify({"success": True})



# ── LOB Settings CRUD ────────────────────────────────────────

@settings_bp.route("/customization/lob-settings/save", methods=["POST"])
@admin_required
def save_lob_setting():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LOBSetting
    data = request.get_json(silent=True) or {}
    setting_id = data.get("id")
    pu_id = data.get("planning_unit_id")

    if setting_id:
        setting = LOBSetting.query.get(setting_id)
        if not setting:
            return jsonify({"success": False, "error": "LOB setting not found"})
    else:
        if not pu_id:
            return jsonify({"success": False, "error": "Planning unit is required"})
        existing = LOBSetting.query.filter_by(planning_unit_id=pu_id).first()
        if existing:
            setting = existing
        else:
            setting = LOBSetting(planning_unit_id=pu_id)
            db.session.add(setting)

    # Update fields
    for field in ["service_level_target", "target_asa", "shrinkage_pct",
                  "occupancy_target", "max_occupancy", "default_shift_hrs"]:
        if field in data:
            setattr(setting, field, float(data[field]))
    if "interval_minutes" in data:
        setting.interval_minutes = int(data["interval_minutes"])
    for field in ["operating_start", "operating_end",
                  "sat_operating_start", "sat_operating_end",
                  "sun_operating_start", "sun_operating_end"]:
        if field in data:
            val = (data[field] or "").strip()
            setattr(setting, field, val if val else None)

    db.session.commit()
    return jsonify({"success": True, "setting": setting.to_dict()})


@settings_bp.route("/customization/lob-settings/delete", methods=["POST"])
@admin_required
def delete_lob_setting():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LOBSetting
    data = request.get_json(silent=True) or {}
    setting = LOBSetting.query.get(data.get("id"))
    if not setting:
        return jsonify({"success": False, "error": "LOB setting not found"})
    db.session.delete(setting)
    db.session.commit()
    return jsonify({"success": True})


# ── Time-Off Types CRUD ──────────────────────────────────────

@settings_bp.route("/customization/time-off-types/save", methods=["POST"])
@admin_required
def save_time_off_type():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, TimeOffType
    data = request.get_json(silent=True) or {}
    tid = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if tid:
        t = TimeOffType.query.get(tid)
        if not t:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if TimeOffType.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        t = TimeOffType(code=code)
        db.session.add(t)

    t.code = code; t.label = label
    t.color = data.get("color", t.color if tid else "#6b7280")
    t.is_paid = bool(data.get("is_paid", True))
    t.requires_approval = bool(data.get("requires_approval", True))
    t.max_days_per_year = data.get("max_days_per_year") or None
    t.min_notice_days = int(data.get("min_notice_days", 0))
    t.sort_order = int(data.get("sort_order", 0))
    db.session.commit()
    return jsonify({"success": True, "item": t.to_dict()})


@settings_bp.route("/customization/time-off-types/delete", methods=["POST"])
@admin_required
def delete_time_off_type():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, TimeOffType
    data = request.get_json(silent=True) or {}
    t = TimeOffType.query.get(data.get("id"))
    if not t:
        return jsonify({"success": False, "error": "Not found"})
    if t.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default type"})
    db.session.delete(t)
    db.session.commit()
    return jsonify({"success": True})


# ── Overtime Rules CRUD ──────────────────────────────────────

@settings_bp.route("/customization/overtime-rules/save", methods=["POST"])
@admin_required
def save_overtime_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, OvertimeRule
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if rid:
        r = OvertimeRule.query.get(rid)
        if not r:
            return jsonify({"success": False, "error": "Not found"})
    else:
        r = OvertimeRule(name=name)
        db.session.add(r)

    r.name = name
    r.rule_type = data.get("rule_type", r.rule_type if rid else "voluntary")
    r.max_ot_hours_week = float(data.get("max_ot_hours_week", 10))
    r.max_ot_hours_day = float(data.get("max_ot_hours_day", 4))
    r.requires_approval = bool(data.get("requires_approval", True))
    r.min_notice_hours = int(data.get("min_notice_hours", 24))
    r.eligible_after_days = int(data.get("eligible_after_days", 90))
    r.pay_multiplier = float(data.get("pay_multiplier", 1.5))
    r.blackout_dates_json = json.dumps(data.get("blackout_dates", []))
    db.session.commit()
    return jsonify({"success": True, "item": r.to_dict()})


@settings_bp.route("/customization/overtime-rules/delete", methods=["POST"])
@admin_required
def delete_overtime_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, OvertimeRule
    data = request.get_json(silent=True) or {}
    r = OvertimeRule.query.get(data.get("id"))
    if not r:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(r)
    db.session.commit()
    return jsonify({"success": True})


# ── Schedule Rules CRUD ──────────────────────────────────────

@settings_bp.route("/customization/schedule-rules/save", methods=["POST"])
@admin_required
def save_schedule_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ScheduleRule
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if rid:
        r = ScheduleRule.query.get(rid)
        if not r:
            return jsonify({"success": False, "error": "Not found"})
    else:
        r = ScheduleRule(name=name)
        db.session.add(r)

    r.name = name
    r.min_hours_week = float(data.get("min_hours_week", 20))
    r.max_hours_week = float(data.get("max_hours_week", 40))
    r.max_hours_day = float(data.get("max_hours_day", 10))
    r.max_consecutive_days = int(data.get("max_consecutive_days", 6))
    r.min_rest_between_shifts_hrs = float(data.get("min_rest_between_shifts_hrs", 10))
    r.min_days_off_per_week = int(data.get("min_days_off_per_week", 1))
    r.max_split_shifts_week = int(data.get("max_split_shifts_week", 0))
    r.allow_back_to_back = bool(data.get("allow_back_to_back", False))
    db.session.commit()
    return jsonify({"success": True, "item": r.to_dict()})


@settings_bp.route("/customization/schedule-rules/delete", methods=["POST"])
@admin_required
def delete_schedule_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, ScheduleRule
    data = request.get_json(silent=True) or {}
    r = ScheduleRule.query.get(data.get("id"))
    if not r:
        return jsonify({"success": False, "error": "Not found"})
    if r.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default rule"})
    db.session.delete(r)
    db.session.commit()
    return jsonify({"success": True})


# ── Holidays CRUD ────────────────────────────────────────────

@settings_bp.route("/customization/holidays/save", methods=["POST"])
@admin_required
def save_holiday():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, Holiday
    data = request.get_json(silent=True) or {}
    hid = data.get("id")
    name = (data.get("name") or "").strip()
    date_str = (data.get("date") or "").strip()
    if not name or not date_str:
        return jsonify({"success": False, "error": "Name and date are required"})

    from datetime import datetime as dt
    hdate = dt.strptime(date_str, "%Y-%m-%d").date()

    if hid:
        h = Holiday.query.get(hid)
        if not h:
            return jsonify({"success": False, "error": "Not found"})
    else:
        h = Holiday(name=name, date=hdate, year=hdate.year)
        db.session.add(h)

    h.name = name; h.date = hdate; h.year = hdate.year
    h.is_full_day = bool(data.get("is_full_day", True))
    h.start_time = data.get("start_time") or None
    h.end_time = data.get("end_time") or None
    h.is_paid = bool(data.get("is_paid", True))
    h.affects_forecast = bool(data.get("affects_forecast", True))
    h.volume_factor = float(data.get("volume_factor", 0.0))
    h.is_recurring = bool(data.get("is_recurring", True))
    db.session.commit()
    return jsonify({"success": True, "item": h.to_dict()})


@settings_bp.route("/customization/holidays/delete", methods=["POST"])
@admin_required
def delete_holiday():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, Holiday
    data = request.get_json(silent=True) or {}
    h = Holiday.query.get(data.get("id"))
    if not h:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(h)
    db.session.commit()
    return jsonify({"success": True})


# ── Skill Groups CRUD ───────────────────────────────────────

@settings_bp.route("/customization/skill-groups/save", methods=["POST"])
@admin_required
def save_skill_group():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SkillGroup
    data = request.get_json(silent=True) or {}
    gid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if gid:
        g = SkillGroup.query.get(gid)
        if not g:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if SkillGroup.query.filter_by(name=name).first():
            return jsonify({"success": False, "error": f"'{name}' already exists"})
        g = SkillGroup(name=name)
        db.session.add(g)

    old_name = g.name if gid else None
    g.name = name
    g.description = (data.get("description") or "").strip()

    # Cascade rename to employees assigned to this skill group
    if old_name and old_name != name:
        from app.models import Employee, SkillMapping, PlanningUnit
        # Update all_skills text field for employees with this skill mapping
        mapped_emp_ids = [m.employee_id for m in
                         SkillMapping.query.filter_by(skill_group_id=g.id).all()]
        if mapped_emp_ids:
            for emp in Employee.query.filter(Employee.id.in_(mapped_emp_ids)).all():
                if emp.all_skills:
                    skills = [s.strip() for s in emp.all_skills.split(",")]
                    skills = [name if s == old_name else s for s in skills]
                    emp.all_skills = ", ".join(skills)
                # Update planning_unit_id if it pointed at the old name
                if emp.planning_unit and emp.planning_unit.name == old_name:
                    new_pu = PlanningUnit.query.filter_by(name=name).first()
                    if new_pu:
                        emp.planning_unit_id = new_pu.id

        # Rename the PlanningUnit itself if one matches the old name
        old_pu = PlanningUnit.query.filter_by(name=old_name).first()
        if old_pu:
            existing_new = PlanningUnit.query.filter_by(name=name).first()
            if not existing_new:
                old_pu.name = name
            else:
                # Merge: reassign employees from old PU to existing new PU
                Employee.query.filter_by(planning_unit_id=old_pu.id).update(
                    {"planning_unit_id": existing_new.id})

        # Update LobMapping targets that reference the old name
        from app.models import LobMapping
        LobMapping.query.filter_by(planning_unit_name=old_name).update(
            {"planning_unit_name": name})

    db.session.commit()
    return jsonify({"success": True, "item": g.to_dict()})


@settings_bp.route("/customization/skill-groups/delete", methods=["POST"])
@admin_required
def delete_skill_group():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SkillGroup
    data = request.get_json(silent=True) or {}
    g = SkillGroup.query.get(data.get("id"))
    if not g:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(g)
    db.session.commit()
    return jsonify({"success": True})


# ── Adherence Exception Codes CRUD ───────────────────────────

@settings_bp.route("/customization/adherence-exceptions/save", methods=["POST"])
@admin_required
def save_adherence_exception():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AdherenceException
    data = request.get_json(silent=True) or {}
    aid = data.get("id")
    code = (data.get("code") or "").strip().lower().replace(" ", "_")
    label = (data.get("label") or "").strip()
    if not code or not label:
        return jsonify({"success": False, "error": "Code and label are required"})

    if aid:
        a = AdherenceException.query.get(aid)
        if not a:
            return jsonify({"success": False, "error": "Not found"})
    else:
        if AdherenceException.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        a = AdherenceException(code=code)
        db.session.add(a)

    a.code = code; a.label = label
    a.color = data.get("color", a.color if aid else "#ef4444")
    a.is_excused = bool(data.get("is_excused", False))
    a.category = data.get("category", "other")
    a.sort_order = int(data.get("sort_order", 0))
    db.session.commit()
    return jsonify({"success": True, "item": a.to_dict()})


@settings_bp.route("/customization/adherence-exceptions/delete", methods=["POST"])
@admin_required
def delete_adherence_exception():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AdherenceException
    data = request.get_json(silent=True) or {}
    a = AdherenceException.query.get(data.get("id"))
    if not a:
        return jsonify({"success": False, "error": "Not found"})
    if a.is_default:
        return jsonify({"success": False, "error": "Cannot delete a default code"})
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


# ── Alert Configs CRUD ───────────────────────────────────────

@settings_bp.route("/customization/alerts/save", methods=["POST"])
@admin_required
def save_alert_config():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AlertConfig
    data = request.get_json(silent=True) or {}
    aid = data.get("id")
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name is required"})

    if aid:
        a = AlertConfig.query.get(aid)
        if not a:
            return jsonify({"success": False, "error": "Not found"})
    else:
        a = AlertConfig(name=name, alert_type=data.get("alert_type", "sl_breach"))
        db.session.add(a)

    a.name = name
    a.alert_type = data.get("alert_type", a.alert_type)
    a.threshold_value = float(data.get("threshold_value", 0.80))
    a.threshold_operator = data.get("threshold_operator", "lt")
    a.planning_unit_id = data.get("planning_unit_id") or None
    a.notify_email = bool(data.get("notify_email", False))
    a.notify_in_app = bool(data.get("notify_in_app", True))
    a.email_recipients = (data.get("email_recipients") or "").strip()
    a.cooldown_minutes = int(data.get("cooldown_minutes", 30))
    a.is_active = bool(data.get("is_active", True))
    db.session.commit()
    return jsonify({"success": True, "item": a.to_dict()})


@settings_bp.route("/customization/alerts/delete", methods=["POST"])
@admin_required
def delete_alert_config():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, AlertConfig
    data = request.get_json(silent=True) or {}
    a = AlertConfig.query.get(data.get("id"))
    if not a:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(a)
    db.session.commit()
    return jsonify({"success": True})


# ── Branding Settings ────────────────────────────────────────

@settings_bp.route("/customization/branding/save", methods=["POST"])
@admin_required
def save_branding():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, BrandSetting
    data = request.get_json(silent=True) or {}

    brand = BrandSetting.query.first()
    if not brand:
        brand = BrandSetting()
        db.session.add(brand)

    brand.company_name = (data.get("company_name") or "").strip()
    brand.tagline = (data.get("tagline") or "").strip()
    brand.accent_color = data.get("accent_color", "#2563eb")
    brand.contact_email = (data.get("contact_email") or "").strip()
    brand.logo_url = (data.get("logo_url") or "").strip()
    brand.favicon_url = (data.get("favicon_url") or "").strip()
    brand.footer_text = (data.get("footer_text") or "").strip()
    db.session.commit()
    return jsonify({"success": True, "brand": brand.to_dict()})


# ── LOB Mappings CRUD ──────────────────────────────────────────

@settings_bp.route("/customization/lob-mappings/save", methods=["POST"])
@admin_required
def save_lob_mapping():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LobMapping
    data = request.get_json(silent=True) or {}
    mid = data.get("id")
    source = (data.get("source_name") or "").strip()
    target = (data.get("planning_unit_name") or "").strip()
    if not source or not target:
        return jsonify({"success": False, "error": "Both source name and planning unit are required"})

    if mid:
        m = LobMapping.query.get(mid)
        if not m:
            return jsonify({"success": False, "error": "Not found"})
    else:
        existing = LobMapping.query.filter_by(source_name=source).first()
        if existing:
            return jsonify({"success": False, "error": f"'{source}' is already mapped"})
        m = LobMapping(source_name=source)
        db.session.add(m)

    m.source_name = source
    m.planning_unit_name = target
    db.session.commit()
    return jsonify({"success": True, "item": {
        "id": m.id, "source_name": m.source_name,
        "planning_unit_name": m.planning_unit_name,
    }})


@settings_bp.route("/customization/lob-mappings/delete", methods=["POST"])
@admin_required
def delete_lob_mapping():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, LobMapping
    data = request.get_json(silent=True) or {}
    m = LobMapping.query.get(data.get("id"))
    if not m:
        return jsonify({"success": False, "error": "Not found"})
    db.session.delete(m)
    db.session.commit()
    return jsonify({"success": True})
