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
    employee_sheet_key = AppSetting.get("employee_sheet_key", "")
    return render_template("settings/google_sheets.html",
        user=get_current_user(),
        sheets_key=sheets_key,
        has_creds=has_creds,
        sheets_status=sheets_status,
        employee_sheet_key=employee_sheet_key,
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
    # Save employee sheet key (for HR system sync)
    emp_sheet_key = data.get("employee_sheet_key", "").strip()
    if emp_sheet_key:
        AppSetting.set("employee_sheet_key", emp_sheet_key)
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
                    "message": message, "error": message if not success else ""})


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
    from app.models import User, Employee
    all_users = User.query.order_by(User.created_at.desc()).all()
    all_employees = Employee.query.filter_by(status="Active").order_by(Employee.last_name).all()
    return render_template("settings/users.html",
        user=get_current_user(),
        users=all_users,
        employees=all_employees,
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
    role = data.get("role", "supervisor")
    password = data.get("password", "")
    is_demo = bool(data.get("is_demo", False))

    if not email:
        return jsonify({"success": False, "error": "Email is required"})
    if role not in ("supervisor", "admin", "agent"):
        role = "supervisor"

    # Resolve linked employee for any role
    employee_id = data.get("employee_id")
    if employee_id:
        from app.models import Employee
        emp = Employee.query.get(employee_id)
        if not emp:
            return jsonify({"success": False, "error": "Selected employee not found"})

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
        # Link employee record
        try:
            user.employee_id = int(employee_id) if employee_id else None
        except Exception:
            pass
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

        # Auto-create an employee profile if no existing one was selected
        from app.models import Employee
        if not employee_id:
            display = name or email.split("@")[0].title()
            parts = display.split(None, 1)
            first = parts[0] if parts else display
            last = parts[1] if len(parts) > 1 else ""
            # Generate a unique employee_id from email prefix
            emp_id_base = email.split("@")[0].replace(".", "").replace("-", "")[:12].upper()
            emp_id_str = emp_id_base
            counter = 1
            while Employee.query.filter_by(employee_id=emp_id_str).first():
                emp_id_str = f"{emp_id_base}{counter}"
                counter += 1
            emp = Employee(
                employee_id=emp_id_str,
                first_name=first,
                last_name=last,
                email=email,
                status="Active",
            )
            db.session.add(emp)
            db.session.flush()  # get emp.id
            employee_id = emp.id

        user = User(
            email=email,
            password_hash=generate_password_hash(password).decode("utf-8"),
            display_name=name or email.split("@")[0].title(),
            role=role,
        )
        try:
            user.employee_id = int(employee_id) if employee_id else None
        except Exception:
            pass
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

    try:
        from app.audit import audit_log
        act = "update" if user_id else "create"
        audit_log(act, "user", user.id, f"{act.title()} user: {email} ({role})")
    except Exception:
        pass

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
    try:
        from zoneinfo import ZoneInfo
        cur_year = dt.now(ZoneInfo("US/Eastern")).year
    except Exception:
        cur_year = dt.utcnow().year
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


# ═══════════════════════════════════════════════════════════════
# ACTIVITIES — helpers
# ═══════════════════════════════════════════════════════════════

def _save_activity_skills(segment_code, skills_data):
    """Sync skills for an activity (replace all)."""
    from app.models import ActivitySkill, db
    # Remove existing
    ActivitySkill.query.filter_by(segment_code_id=segment_code.id).delete()
    for sk in (skills_data or []):
        name = (sk.get("name") or "").strip()
        if not name:
            continue
        db.session.add(ActivitySkill(
            segment_code_id=segment_code.id,
            name=name,
            weighting=int(sk.get("weighting", 100)),
        ))


def _save_external_status_mappings(segment_code, mappings_data):
    """Sync external status mappings for an activity (replace all)."""
    from app.models import ExternalStatusMapping, db
    ExternalStatusMapping.query.filter_by(segment_code_id=segment_code.id).delete()
    for m in (mappings_data or []):
        ext = (m.get("external_status") or "").strip()
        if not ext:
            continue
        db.session.add(ExternalStatusMapping(
            segment_code_id=segment_code.id,
            external_status=ext,
            description=(m.get("description") or "").strip() or None,
        ))


# ═══════════════════════════════════════════════════════════════
# ACTIVITIES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/activities")
@admin_required
def activities():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_activities
        return render_template("settings/activities.html", items=get_demo_activities())
    from app.models import SegmentCode
    all_items = SegmentCode.query.filter_by(is_active=True).order_by(SegmentCode.sort_order, SegmentCode.label).all()
    items = [s.to_dict() for s in all_items]
    # Build parent options for multi-activity dropdown
    parents = [{"id": s.id, "label": s.label} for s in all_items if s.is_multi_activity]
    return render_template("settings/activities.html", items=items, parents=parents)


@settings_bp.route("/activities/save", methods=["POST"])
@admin_required
def activities_save():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import SegmentCode, db
    data = request.json or {}
    try:
        label = (data.get("label") or "").strip()
        code = (data.get("code") or "").strip().lower().replace(" ", "_")
        if not code and label:
            code = label.lower().replace(" ", "_")
            code = "".join(c for c in code if c.isalnum() or c == "_").strip("_")
        if not code or not label:
            return jsonify(success=False, error="Code and label are required")

        # Parse external IDs from comma-separated string or list
        raw_ext = data.get("external_ids", [])
        if isinstance(raw_ext, str):
            ext_ids = [x.strip() for x in raw_ext.split(",") if x.strip()]
        elif isinstance(raw_ext, list):
            ext_ids = [str(x).strip() for x in raw_ext if str(x).strip()]
        else:
            ext_ids = []

        parent_id = data.get("parent_id")
        if parent_id:
            parent_id = int(parent_id)
        else:
            parent_id = None
        is_multi = bool(data.get("is_multi_activity", False))
        activity_category = data.get("activity_category", "status")
        if activity_category not in ("status", "lob"):
            activity_category = "status"

        # Parse activity type
        activity_type = data.get("activity_type", "presence")
        if activity_type not in ("presence", "break", "absence", "meeting", "vacation"):
            activity_type = "presence"

        seg_id = data.get("id")
        if seg_id:
            item = SegmentCode.query.get(int(seg_id))
            if not item:
                return jsonify(success=False, error="Segment not found")
            item.code = code
            item.label = label
            item.color = data.get("color", item.color)
            item.is_productive = bool(data.get("is_productive", item.is_productive))
            item.is_paid = bool(data.get("is_paid", item.is_paid))
            item.sort_order = int(data.get("sort_order", item.sort_order))
            item.offset_mins = data.get("offset_mins")
            item.duration_mins = data.get("duration_mins")
            item.is_flexible = bool(data.get("is_flexible", False))
            item.window_start_mins = data.get("window_start_mins")
            item.window_end_mins = data.get("window_end_mins")
            item.activity_category = activity_category
            item.activity_type = activity_type
            item.official_name = (data.get("official_name") or "").strip() or None
            item.abbreviation = (data.get("abbreviation") or "").strip() or None
            item.shortcut = (data.get("shortcut") or "").strip() or None
            item.is_replaceable = bool(data.get("is_replaceable", True))
            item.is_plannable = bool(data.get("is_plannable", True))
            item.importance = int(data.get("importance", 50))
            item.priority = int(data.get("priority", 50))
            item.comply_rest_period = bool(data.get("comply_rest_period", True))
            item.allow_overstaffing_zero = bool(data.get("allow_overstaffing_zero", False))
            item.is_requestable = bool(data.get("is_requestable", False))
            item.is_exchangeable = bool(data.get("is_exchangeable", False))
            item.allow_full_day = bool(data.get("allow_full_day", False))
            item.special_handling = bool(data.get("special_handling", False))
            item.can_be_day_status = bool(data.get("can_be_day_status", False))
            item.set_external_ids(ext_ids)
            item.parent_id = parent_id
            item.is_multi_activity = is_multi
        else:
            if SegmentCode.query.filter_by(code=code).first():
                return jsonify(success=False, error=f"Code '{code}' already exists")
            item = SegmentCode(
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
                activity_category=activity_category,
                activity_type=activity_type,
                official_name=(data.get("official_name") or "").strip() or None,
                abbreviation=(data.get("abbreviation") or "").strip() or None,
                shortcut=(data.get("shortcut") or "").strip() or None,
                is_replaceable=bool(data.get("is_replaceable", True)),
                is_plannable=bool(data.get("is_plannable", True)),
                importance=int(data.get("importance", 50)),
                priority=int(data.get("priority", 50)),
                comply_rest_period=bool(data.get("comply_rest_period", True)),
                allow_overstaffing_zero=bool(data.get("allow_overstaffing_zero", False)),
                is_requestable=bool(data.get("is_requestable", False)),
                is_exchangeable=bool(data.get("is_exchangeable", False)),
                allow_full_day=bool(data.get("allow_full_day", False)),
                special_handling=bool(data.get("special_handling", False)),
                can_be_day_status=bool(data.get("can_be_day_status", False)),
                parent_id=parent_id,
                is_multi_activity=is_multi,
            )
            item.set_external_ids(ext_ids)
            db.session.add(item)

        # Handle skills
        db.session.flush()  # ensure item.id is set
        _save_activity_skills(item, data.get("skills", []))
        _save_external_status_mappings(item, data.get("external_statuses", []))
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/activities/delete", methods=["POST"])
@admin_required
def activities_delete():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import SegmentCode, db
    d = request.json or {}
    try:
        item = SegmentCode.query.get(int(d["id"]))
        if not item:
            return jsonify(success=False, error="Segment not found")
        if item.is_default:
            return jsonify(success=False, error="Cannot delete a default segment code")
        db.session.delete(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# CONTRACTS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/contracts")
@admin_required
def contracts():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_contracts
        return render_template("settings/contracts.html", items=get_demo_contracts(), schedule_rules=[])
    from app.models import Contract, ScheduleRule
    items = [c.to_dict() for c in Contract.query.order_by(Contract.name).all()]
    schedule_rules = ScheduleRule.query.filter_by(is_active=True).order_by(ScheduleRule.name).all()
    return render_template("settings/contracts.html", items=items, schedule_rules=schedule_rules)


@settings_bp.route("/contracts/save", methods=["POST"])
@admin_required
def contracts_save():
    from app.models import Contract, db
    d = request.json or {}
    try:
        item = Contract.query.get(int(d["id"])) if d.get("id") else Contract()
        item.name = d["name"]

        # String fields
        for fld in ["abbreviation", "color", "contract_type", "workdays_calculation",
                     "work_hours_mon", "work_hours_tue", "work_hours_wed", "work_hours_thu",
                     "work_hours_fri", "work_hours_sat", "work_hours_sun",
                     "min_net_work_hours_day", "max_net_work_hours_day",
                     "rest_between_workdays", "max_activity_duration",
                     "min_gap_between_activities", "max_gap_between_activities",
                     "max_work_hours_24h", "overtime_threshold_consec",
                     "weekly_rest_no_full_day", "weekly_rest_full_day",
                     "max_activity_duration_2", "rest_after_holiday_no_full",
                     "rest_after_holiday_full", "max_work_time_deviation"]:
            if fld in d:
                setattr(item, fld, d[fld] or None)

        # Float fields
        for fld in ["weekly_hours", "daily_hours_min", "daily_hours_target", "daily_hours_max",
                     "weekly_hours_min", "weekly_hours_target", "monthly_hours_max",
                     "min_rest_hours", "break_after_hours"]:
            if fld in d and d[fld] is not None and d[fld] != '':
                setattr(item, fld, float(d[fld]))
            elif fld in d:
                setattr(item, fld, None)

        # Integer fields
        for fld in ["days_per_week", "min_days_per_week", "max_days_per_week",
                     "break_duration_mins", "lunch_duration_mins", "max_consecutive_days",
                     "min_days_off_per_week", "min_consec_days_off_week",
                     "max_consecutive_days_off", "weeks_max_1_sat",
                     "max_saturdays_per_month", "max_sundays_per_month",
                     "max_shifts_per_day", "min_weekends_off_month",
                     "max_working_days_per_week", "max_consecutive_working_days",
                     "min_consec_days_off_week_sched", "min_days_off_sat_work",
                     "min_days_off_sun_work", "overtime_num_weeks",
                     "max_night_shifts_week", "max_night_shifts_month",
                     "max_consec_night_shifts", "max_sun_holidays_month",
                     "comp_eligibility_weekend", "max_sundays_in_row",
                     "max_weekends_in_row", "max_day_models_24h"]:
            if fld in d and d[fld] is not None and d[fld] != '':
                setattr(item, fld, int(d[fld]))
            elif fld in d:
                setattr(item, fld, None)

        # Boolean fields
        for fld in ["overtime_eligible", "is_active", "use_target_work_times", "schedule_after_day_off",
                     "exclude_illness", "exclude_vacation",
                     "max_work_hours_per_day_flag", "max_work_hours_include_activities",
                     "max_work_hours_include_day_models",
                     "exclude_illness_consec", "exclude_vacation_consec",
                     "no_schedule_on_holidays", "avoid_overlap_sun_rule"]:
            if fld in d:
                setattr(item, fld, bool(d[fld]))

        item.schedule_rule_id = int(d["schedule_rule_id"]) if d.get("schedule_rule_id") else None
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/contracts/delete", methods=["POST"])
@admin_required
def contracts_delete():
    from app.models import Contract, db
    d = request.json or {}
    try:
        item = Contract.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# DAY MODELS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/day-models")
@admin_required
def day_models():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_day_models
        return render_template("settings/day_models.html", items=get_demo_day_models(), activities=[], planning_units=[], shift_templates=[])
    from app.models import DayModel, Activity, PlanningUnit, ShiftTemplate
    items = [dm.to_dict() for dm in DayModel.query.order_by(DayModel.sort_order, DayModel.name).all()]
    activities = [a.to_dict() for a in Activity.query.filter_by(is_active=True).order_by(Activity.name).all()]
    planning_units = PlanningUnit.query.filter_by(is_active=True).order_by(PlanningUnit.name).all()
    shift_templates = ShiftTemplate.query.filter_by(is_active=True).order_by(ShiftTemplate.name).all()
    return render_template("settings/day_models.html", items=items, activities=activities,
                           planning_units=planning_units, shift_templates=shift_templates)


@settings_bp.route("/day-models/save", methods=["POST"])
@admin_required
def day_models_save():
    from app.models import DayModel, db
    d = request.json or {}
    try:
        item = DayModel.query.get(int(d["id"])) if d.get("id") else DayModel()
        item.name = d["name"]
        item.abbreviation = d.get("abbreviation", "")
        item.start_time = d.get("start_time", "08:00")
        item.end_time = d.get("end_time", "16:30")
        item.paid_hours = float(d.get("paid_hours", 8))
        item.total_hours = float(d.get("total_hours", 0)) or item.paid_hours
        item.model_type = d.get("model_type", "Fixed")
        item.color = d.get("color", "#4472C4")
        item.day_type = d.get("day_type", "any")
        item.planning_unit_id = int(d["planning_unit_id"]) if d.get("planning_unit_id") else None
        item.shift_template_id = int(d["shift_template_id"]) if d.get("shift_template_id") else None
        item.activities_json = json.dumps(d.get("activities", []))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/day-models/delete", methods=["POST"])
@admin_required
def day_models_delete():
    from app.models import DayModel, db
    d = request.json or {}
    try:
        item = DayModel.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# PLANNING UNITS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/planning-units")
@admin_required
def planning_units_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_planning_units_config
        return render_template("settings/planning_units.html", items=get_demo_planning_units_config(), activities=[])
    from app.models import PlanningUnit, Employee, SegmentCode
    pus = PlanningUnit.query.order_by(PlanningUnit.name).all()
    items = []
    for pu in pus:
        d = pu.to_dict()
        d["employee_count"] = Employee.query.filter_by(planning_unit_id=pu.id).count()
        items.append(d)
    # Available activities for assignment dropdown
    activities = [s.to_dict() for s in SegmentCode.query.filter_by(is_active=True).order_by(SegmentCode.label).all()]
    return render_template("settings/planning_units.html", items=items, activities=activities)


@settings_bp.route("/planning-units/save", methods=["POST"])
@admin_required
def planning_units_save():
    from app.models import PlanningUnit, PlanningUnitBusinessHours, PlanningUnitActivity, PlanningUnitParameter, CallRoute, db
    d = request.json or {}
    try:
        if d.get("id"):
            item = PlanningUnit.query.get(int(d["id"]))
            if not item:
                return jsonify(success=False, error="Not found")
        else:
            item = PlanningUnit()
            db.session.add(item)
        item.name = (d.get("name") or "").strip()
        if not item.name:
            return jsonify(success=False, error="Name is required")
        item.description = (d.get("description") or "").strip() or None
        item.timezone = (d.get("timezone") or "America/New_York").strip()
        item.is_active = bool(d.get("is_active", True))
        db.session.flush()

        # Sync business hours (replace all)
        PlanningUnitBusinessHours.query.filter_by(planning_unit_id=item.id).delete()
        for bh in (d.get("business_hours") or []):
            day_type = (bh.get("day_type") or "").strip()
            if not day_type:
                continue
            db.session.add(PlanningUnitBusinessHours(
                planning_unit_id=item.id,
                day_type=day_type,
                open_time=bh.get("open_time", "08:00"),
                close_time=bh.get("close_time", "22:00"),
                valid_from=_parse_date(bh.get("valid_from")),
                valid_to=_parse_date(bh.get("valid_to")),
            ))

        # Sync assigned activities (replace all)
        PlanningUnitActivity.query.filter_by(planning_unit_id=item.id).delete()
        for pa in (d.get("assigned_activities") or []):
            sc_id = pa.get("segment_code_id")
            if not sc_id:
                continue
            db.session.add(PlanningUnitActivity(
                planning_unit_id=item.id,
                segment_code_id=int(sc_id),
                window_start=pa.get("window_start") or None,
                window_end=pa.get("window_end") or None,
                valid_from=_parse_date(pa.get("valid_from")),
                valid_to=_parse_date(pa.get("valid_to")),
            ))

        # Sync call routes (replace all)
        CallRoute.query.filter_by(planning_unit_id=item.id).delete()
        for cr in (d.get("call_routes") or []):
            route_id = (cr.get("route_id") or "").strip()
            route_name = (cr.get("name") or "").strip()
            if not route_id or not route_name:
                continue
            db.session.add(CallRoute(
                planning_unit_id=item.id,
                route_id=route_id,
                name=route_name,
                is_active=bool(cr.get("is_active", True)),
            ))

        # Sync parameters (replace all)
        PlanningUnitParameter.query.filter_by(planning_unit_id=item.id).delete()
        for p in (d.get("parameters") or []):
            name = (p.get("name") or "").strip()
            if not name:
                continue
            db.session.add(PlanningUnitParameter(
                planning_unit_id=item.id,
                name=name,
                lower_limit=float(p["lower_limit"]) if p.get("lower_limit") not in (None, "") else None,
                upper_limit=float(p["upper_limit"]) if p.get("upper_limit") not in (None, "") else None,
            ))

        db.session.commit()
        return jsonify(success=True, id=item.id)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


def _parse_date(val):
    """Parse a date string (YYYY-MM-DD) to a date object, or None."""
    if not val:
        return None
    from datetime import date as dt_date
    try:
        parts = str(val).split("-")
        return dt_date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


@settings_bp.route("/planning-units/delete", methods=["POST"])
@admin_required
def planning_units_delete():
    from app.models import PlanningUnit, Employee, db
    d = request.json or {}
    try:
        item = PlanningUnit.query.get(int(d["id"]))
        if item:
            Employee.query.filter_by(planning_unit_id=item.id).update({"planning_unit_id": None})
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SKILLS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/skills")
@admin_required
def skills_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_skills_config
        return render_template("settings/skills.html", items=get_demo_skills_config())
    from app.models import SkillGroup, SkillMapping
    groups = SkillGroup.query.order_by(SkillGroup.name).all()
    items = []
    for g in groups:
        items.append({
            "id": g.id, "name": g.name,
            "description": g.description or "",
            "is_active": g.is_active,
            "mapping_count": SkillMapping.query.filter_by(skill_group_id=g.id).count(),
        })
    return render_template("settings/skills.html", items=items)


@settings_bp.route("/skills/save", methods=["POST"])
@admin_required
def skills_save():
    from app.models import SkillGroup, db
    d = request.json or {}
    try:
        item = SkillGroup.query.get(int(d["id"])) if d.get("id") else SkillGroup()
        item.name = d["name"]
        item.description = d.get("description", "")
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/skills/delete", methods=["POST"])
@admin_required
def skills_delete():
    from app.models import SkillGroup, db
    d = request.json or {}
    try:
        item = SkillGroup.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SELECTIONS
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/selections")
@admin_required
def selections_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_selections
        return render_template("settings/selections.html", items=get_demo_selections())
    from app.models import Selection, SelectionMember
    sels = Selection.query.order_by(Selection.name).all()
    items = []
    for s in sels:
        items.append({
            "id": s.id, "name": s.name,
            "description": s.description or "",
            "is_active": s.is_active,
            "member_count": SelectionMember.query.filter_by(selection_id=s.id).count(),
        })
    return render_template("settings/selections.html", items=items)


@settings_bp.route("/selections/save", methods=["POST"])
@admin_required
def selections_save():
    from app.models import Selection, db
    d = request.json or {}
    try:
        item = Selection.query.get(int(d["id"])) if d.get("id") else Selection()
        item.name = d["name"]
        item.description = d.get("description", "")
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/selections/delete", methods=["POST"])
@admin_required
def selections_delete():
    from app.models import Selection, db
    d = request.json or {}
    try:
        item = Selection.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# SHIFT SEQUENCES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/shift-sequences")
@admin_required
def shift_sequences_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_shift_sequences
        data = get_demo_shift_sequences()
        return render_template("settings/shift_sequences.html",
                               items=data["items"],
                               shift_templates=data["shift_templates"])
    from app.models import ShiftSequence, ShiftTemplate
    seqs = ShiftSequence.query.order_by(ShiftSequence.name).all()
    items = [s.to_dict() for s in seqs]
    templates = ShiftTemplate.query.filter_by(is_active=True).order_by(ShiftTemplate.name).all()
    shift_templates = [{"id": t.id, "name": t.name, "start": t.start_time, "end": t.end_time} for t in templates]
    return render_template("settings/shift_sequences.html",
                           items=items, shift_templates=shift_templates)


@settings_bp.route("/shift-sequences/save", methods=["POST"])
@admin_required
def shift_sequences_save():
    from app.models import ShiftSequence, db
    d = request.json or {}
    try:
        item = ShiftSequence.query.get(int(d["id"])) if d.get("id") else ShiftSequence()
        item.name = d["name"]
        item.cycle_weeks = int(d.get("cycle_weeks", 1))
        item.is_active = bool(d.get("is_active", True))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/shift-sequences/delete", methods=["POST"])
@admin_required
def shift_sequences_delete():
    from app.models import ShiftSequence, db
    d = request.json or {}
    try:
        item = ShiftSequence.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/shift-sequences/<int:seq_id>/rows")
@admin_required
def shift_sequence_rows(seq_id):
    from app.models import ShiftSequenceRow
    rows = ShiftSequenceRow.query.filter_by(shift_sequence_id=seq_id)\
           .order_by(ShiftSequenceRow.position).all()
    return jsonify([r.to_dict() for r in rows])


@settings_bp.route("/shift-sequences/save-rows", methods=["POST"])
@admin_required
def shift_sequence_save_rows():
    import json as _json
    from app.models import ShiftSequenceRow, db
    d = request.json or {}
    seq_id = d.get("sequence_id")
    rows_data = d.get("rows", [])
    try:
        # Delete existing rows and recreate
        ShiftSequenceRow.query.filter_by(shift_sequence_id=seq_id).delete()
        for i, rd in enumerate(rows_data):
            row = ShiftSequenceRow(
                shift_sequence_id=seq_id,
                name=rd.get("name", ""),
                position=i,
                pattern_json=_json.dumps(rd.get("pattern", {})),
            )
            db.session.add(row)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# PLANNING CALENDARS & DAY TYPES
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/planning-calendars")
@admin_required
def planning_calendars_page():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_planning_calendars
        data = get_demo_planning_calendars()
        return render_template("settings/planning_calendars.html", day_types=data["day_types"], calendars=data["calendars"])
    from app.models import DayType, PlanningCalendar
    day_types = [dt.to_dict() for dt in DayType.query.order_by(DayType.name).all()]
    calendars = [c.to_dict() for c in PlanningCalendar.query.order_by(PlanningCalendar.name).all()]
    return render_template("settings/planning_calendars.html",
                           day_types=day_types, calendars=calendars)


@settings_bp.route("/planning-calendars/day-types/save", methods=["POST"])
@admin_required
def day_types_save():
    from app.models import DayType, db
    d = request.json or {}
    try:
        item = DayType.query.get(int(d["id"])) if d.get("id") else DayType()
        item.name = d["name"]
        item.color = d.get("color", "#ef4444")
        item.is_holiday = bool(d.get("is_holiday", False))
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/day-types/delete", methods=["POST"])
@admin_required
def day_types_delete():
    from app.models import DayType, db
    d = request.json or {}
    try:
        item = DayType.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/save", methods=["POST"])
@admin_required
def planning_calendars_save():
    from app.models import PlanningCalendar, db
    d = request.json or {}
    try:
        item = PlanningCalendar.query.get(int(d["id"])) if d.get("id") else PlanningCalendar()
        item.name = d["name"]
        if not d.get("id"):
            db.session.add(item)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/delete", methods=["POST"])
@admin_required
def planning_calendars_delete():
    from app.models import PlanningCalendar, db
    d = request.json or {}
    try:
        item = PlanningCalendar.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/<int:cal_id>/entries")
@admin_required
def calendar_entries_list(cal_id):
    from app.models import CalendarEntry
    entries = CalendarEntry.query.filter_by(calendar_id=cal_id)\
              .order_by(CalendarEntry.date).all()
    return jsonify([e.to_dict() for e in entries])


@settings_bp.route("/planning-calendars/entries/save", methods=["POST"])
@admin_required
def calendar_entries_save():
    from datetime import date as _date
    from app.models import CalendarEntry, db
    d = request.json or {}
    try:
        entry = CalendarEntry(
            calendar_id=int(d["calendar_id"]),
            day_type_id=int(d["day_type_id"]),
            date=_date.fromisoformat(d["date"]),
        )
        db.session.add(entry)
        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


@settings_bp.route("/planning-calendars/entries/delete", methods=["POST"])
@admin_required
def calendar_entries_delete():
    from app.models import CalendarEntry, db
    d = request.json or {}
    try:
        item = CalendarEntry.query.get(int(d["id"]))
        if item:
            db.session.delete(item)
            db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e))


# ═══════════════════════════════════════════════════════════════
# MASTER RESET
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/reset", methods=["POST"])
@admin_required
def master_reset():
    """Wipe all operational data, API connections, and settings.

    Preserves: user accounts, brand settings.
    Blocked for demo users.
    """
    dg = _demo_guard()
    if dg:
        return dg

    data = request.get_json(silent=True) or {}
    confirm = data.get("confirm", "")
    if confirm != "RESET":
        return jsonify({"success": False,
                        "error": "Type RESET to confirm."})

    from app.models import (
        db, User, Employee, Accommodation, PTOEntry,
        PlanningUnit, ForecastInterval, RequirementInterval,
        Schedule, ShiftSegment, APIConnection, DataUpload, DataSource,
        AppSetting, SegmentCode, ShiftTemplate, RotationPattern,
        RotationAssignment, FillInRule, LOBSetting, TimeOffType,
        OvertimeRule, ScheduleRule, Holiday, SkillGroup, SkillMapping,
        AdherenceException, AlertConfig, EmployeeAvailability,
        IntervalActual, LobMapping, AgentStatusEvent,
        Activity, Contract, DayModel,
        EmployeePlanningUnit, EmployeeWorkTimePattern,
        EmployeeContract, Selection, SelectionMember,
        ShiftSequence, ShiftSequenceRow, EmployeeShiftSequence,
        DayType, PlanningCalendar, CalendarEntry,
        CoachingSession, QualityEvaluation, AnalyticsIntegration,
        ShiftPost, ShiftBid, ShiftSwapRequest, VTOOTPost, VTOOTSignup,
        WeekTimePattern, WorkTimePatternModel,
    )

    log.info("=== MASTER RESET initiated ===")

    # Unlink users from employee records so employees can be deleted
    try:
        User.query.update({User.employee_id: None})
        db.session.flush()
    except Exception as e:
        log.warning(f"Reset: error unlinking user→employee: {e}")
        db.session.rollback()

    # Order matters — delete children before parents
    tables_to_clear = [
        # Child tables first (all FK refs to employees)
        ShiftSegment,
        Accommodation,
        PTOEntry,
        RotationAssignment,
        SkillMapping,
        EmployeeAvailability,
        EmployeePlanningUnit,
        EmployeeContract,
        EmployeeWorkTimePattern,
        EmployeeShiftSequence,
        SelectionMember,
        AdherenceException,
        AgentStatusEvent,
        IntervalActual,
        CoachingSession,
        QualityEvaluation,
        ShiftBid,
        ShiftSwapRequest,
        VTOOTSignup,
        VTOOTPost,
        ShiftPost,
        # Main data tables
        Schedule,
        ForecastInterval,
        RequirementInterval,
        Employee,
        PlanningUnit,
        # Config tables
        APIConnection,
        DataUpload,
        DataSource,
        SegmentCode,
        ShiftTemplate,
        RotationPattern,
        FillInRule,
        LOBSetting,
        TimeOffType,
        OvertimeRule,
        ScheduleRule,
        Holiday,
        SkillGroup,
        AlertConfig,
        LobMapping,
        Selection,
        # Planning calendars & shift sequences
        CalendarEntry,
        ShiftSequenceRow,
        PlanningCalendar,
        DayType,
        ShiftSequence,
        # Scheduling config tables
        DayModel,
        Activity,
        Contract,
        WeekTimePattern,
        WorkTimePatternModel,
        AnalyticsIntegration,
    ]

    counts = {}
    for model in tables_to_clear:
        try:
            n = model.query.delete()
            counts[model.__tablename__] = n
        except Exception as e:
            log.warning(f"Reset: error clearing {model.__tablename__}: {e}")
            db.session.rollback()

    # Clear app settings (Google Sheet key, etc.) but keep brand
    try:
        AppSetting.query.delete()
        counts["app_settings"] = "cleared"
    except Exception as e:
        log.warning(f"Reset: error clearing app_settings: {e}")
        db.session.rollback()

    db.session.commit()

    total = sum(v for v in counts.values() if isinstance(v, int))
    log.info(f"=== MASTER RESET complete — {total} rows deleted across {len(counts)} tables ===")

    return jsonify({
        "success": True,
        "message": f"Reset complete. {total} records cleared across {len(counts)} tables.",
        "details": counts,
    })


# ═══════════════════════════════════════════════════════════════
# ACTIVITY LOG
# ═══════════════════════════════════════════════════════════════

@settings_bp.route("/activity-log")
@admin_required
def activity_log():
    return render_template("settings/activity_log.html", user=get_current_user())


@settings_bp.route("/activity-log/data", methods=["POST"])
@admin_required
def activity_log_data():
    """Return paginated audit log entries."""
    try:
        from app.models import AuditLog
        payload = request.get_json(silent=True) or {}
        page = int(payload.get("page", 1))
        per_page = min(int(payload.get("per_page", 50)), 100)
        action_filter = payload.get("action", "")

        q = AuditLog.query.order_by(AuditLog.created_at.desc())
        if action_filter:
            q = q.filter(AuditLog.action == action_filter)

        total = q.count()
        entries = q.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            "success": True,
            "entries": [e.to_dict() for e in entries],
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
        })
    except Exception as e:
        log.exception("Activity log error")
        return jsonify({"success": False, "error": str(e)})
