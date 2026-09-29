"""
routes/settings.py — API Connection Manager (Phase 1.3)
========================================================
Admin page for managing external API connections to workforce
management platforms. Supports adding, editing, testing, and
removing API credentials.
"""

import json
import logging
import datetime as _dt
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
    "injixo": {
        "label": "PeopleWare / Injixo",
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
    msg = str(e)
    low = msg.lower()
    if "403" in low or "permission" in low or "does not have permission" in low:
        return ("Google refused access (403). Share the sheet with the service account email "
                "(client_email in the JSON) as Viewer, and make sure the Google Sheets API and "
                "Google Drive API are enabled in the Google Cloud project.")
    if "404" in low or "not found" in low or "spreadsheetnotfound" in low:
        return "Sheet not found (404). Check the Google Sheet key — it's the long ID between /d/ and /edit in the sheet's URL."
    if "invalid_grant" in low or "jwt" in low or "private key" in low or "pem" in low:
        return "The service account JSON was rejected by Google. Re-download the key file and paste the entire contents."
    if "expecting value" in low or "jsondecode" in low:
        return "The service account box does not contain valid JSON — paste the whole file, starting with { and ending with }."
    return f"Could not open the sheet: {msg}"


@settings_bp.route("/google-sheets/test", methods=["POST"])
@admin_required
def test_google_sheets():
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
    """Strip whitespace/newlines from a stored token. PeopleWare tokens are
    used exactly as issued (they look base64 but must NOT be decoded)."""
    return (raw or "").strip().replace("\n", "").replace("\r", "")


@settings_bp.route("/api-connections/test", methods=["POST"])
@admin_required
def test_connection():
    """Test an API connection by attempting a basic request."""
    from app.models import db, APIConnection
    import urllib.request
    import urllib.error
    import ssl

    data = request.get_json(silent=True) or {}
    conn_id = data.get("id")
    if not conn_id:
        return jsonify({"success": False, "error": "No connection ID"})

    conn = APIConnection.query.get(conn_id)
    if not conn:
        return jsonify({"success": False, "error": "Connection not found"})

    if not conn.base_url:
        conn.last_tested = datetime.utcnow()
        conn.last_status = "error"
        conn.last_error = "No base URL configured"
        db.session.commit()
        return jsonify({"success": False, "error": "No base URL configured"})

    try:
        creds = json.loads(conn.credentials) if conn.credentials else {}

        # Build a simple health-check request
        url = conn.base_url.rstrip("/")
        provider = (conn.provider or "").lower()

        # PeopleWare / Injixo: always Bearer, token may be stored base64-encoded,
        # and a bare host needs a real endpoint to answer 200.
        if provider == "injixo":
            token = _clean_token(creds.get("access_token") or creds.get("api_key", ""))
            if url.endswith("peopleware.com") or url.endswith("/people") or url.endswith("/v1"):
                url = "https://legacy-api.peopleware.com/v1/employees"
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Serevo/1.0")
            req.add_header("Accept", "application/json")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        else:
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "Serevo/1.0")

        if provider == "injixo":
            pass
        elif conn.auth_type == "bearer":
            token = _clean_token(creds.get("access_token") or creds.get("api_key", ""))
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        elif conn.auth_type == "basic":
            import base64
            user = creds.get("username", "")
            pwd = creds.get("password", "")
            encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            req.add_header("Authorization", f"Basic {encoded}")
        elif conn.auth_type == "api_key":
            key = creds.get("api_key", "")
            if key:
                req.add_header("X-API-Key", key)

        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        status = resp.status

        conn.last_tested = datetime.utcnow()
        if 200 <= status < 400:
            conn.last_status = "ok"
            conn.last_error = ""
        else:
            conn.last_status = "error"
            conn.last_error = f"HTTP {status}"
        db.session.commit()
        return jsonify({"success": True, "status": conn.last_status, "http": status})

    except urllib.error.HTTPError as e:
        conn.last_tested = datetime.utcnow()
        conn.last_status = "error"
        conn.last_error = f"HTTP {e.code}: {e.reason}"
        db.session.commit()
        return jsonify({"success": False, "error": conn.last_error})
    except Exception as e:
        conn.last_tested = datetime.utcnow()
        conn.last_status = "error"
        conn.last_error = str(e)
        db.session.commit()
        return jsonify({"success": False, "error": str(e)})


@settings_bp.route("/api-connections/delete", methods=["POST"])
@admin_required
def delete_connection():
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

    from app.models import (SegmentCode, ShiftTemplate, RotationPattern, LOBSetting,
                            PlanningUnit, TimeOffType, OvertimeRule, ScheduleRule,
                            Holiday, SkillGroup, AdherenceException, AlertConfig,
                            BrandSetting, Employee, FillInRule, EmployeeAvailability)

    segments = [s.to_dict() for s in SegmentCode.query.order_by(SegmentCode.sort_order, SegmentCode.label).all()]
    shifts = [s.to_dict() for s in ShiftTemplate.query.order_by(ShiftTemplate.sort_order, ShiftTemplate.name).all()]
    rotations = [r.to_dict() for r in RotationPattern.query.order_by(RotationPattern.name).all()]
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

    employees = [{"id": e.id, "employee_id": e.employee_id, "name": e.full_name,
                  "lob": e.planning_unit.name if e.planning_unit else ""}
                 for e in Employee.query.filter_by(status="Active").order_by(Employee.last_name).all()]

    fill_in_rules = [r.to_dict() for r in FillInRule.query.order_by(
        FillInRule.shift_category, FillInRule.priority).all()]

    return render_template("settings/customization.html",
        user=user,
        segments=segments, shifts=shifts, rotations=rotations,
        lob_settings=lob_settings, all_lobs=all_lobs,
        time_off_types=time_off_types, ot_rules=ot_rules,
        sched_rules=sched_rules, holidays=holidays,
        skill_groups=skill_groups, adherence_codes=adherence_codes,
        alerts=alerts, brand_data=brand_data, current_year=cur_year,
        employees=employees, fill_in_rules=fill_in_rules,
        availability=[a.to_dict() for a in EmployeeAvailability.query.order_by(
            EmployeeAvailability.employee_id, EmployeeAvailability.day_of_week).all()],
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


# ── Rotation Patterns CRUD ───────────────────────────────────

@settings_bp.route("/customization/rotations/save", methods=["POST"])
@admin_required
def save_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationPattern
    data = request.get_json(silent=True) or {}
    rot_id = data.get("id")
    name = (data.get("name") or "").strip()
    weeks = data.get("weeks", [])
    if not name:
        return jsonify({"success": False, "error": "Rotation name is required"})
    if not weeks or len(weeks) < 1:
        return jsonify({"success": False, "error": "At least one week is required"})

    if rot_id:
        rot = RotationPattern.query.get(rot_id)
        if not rot:
            return jsonify({"success": False, "error": "Rotation pattern not found"})
        rot.name = name
        rot.weeks_json = json.dumps(weeks)
        rot.cycle_weeks = len(weeks)
    else:
        rot = RotationPattern(
            name=name,
            weeks_json=json.dumps(weeks),
            cycle_weeks=len(weeks),
        )
        db.session.add(rot)
    db.session.commit()
    return jsonify({"success": True, "rotation": rot.to_dict()})


@settings_bp.route("/customization/rotations/delete", methods=["POST"])
@admin_required
def delete_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, RotationPattern
    data = request.get_json(silent=True) or {}
    rot = RotationPattern.query.get(data.get("id"))
    if not rot:
        return jsonify({"success": False, "error": "Rotation pattern not found"})
    db.session.delete(rot)
    db.session.commit()
    return jsonify({"success": True})


# ── Rotation Assignments CRUD ───────────────────────────────

@settings_bp.route("/customization/rotations/assign", methods=["POST"])
@admin_required
def assign_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    """Add an employee to a rotation pattern."""
    from app.models import db, RotationPattern, RotationAssignment, Employee
    data = request.get_json(silent=True) or {}
    rot = RotationPattern.query.get(data.get("rotation_id"))
    if not rot:
        return jsonify({"success": False, "error": "Rotation pattern not found"})
    emp = Employee.query.get(data.get("employee_id"))
    if not emp:
        return jsonify({"success": False, "error": "Employee not found"})
    # Check for existing assignment
    existing = RotationAssignment.query.filter_by(
        rotation_id=rot.id, employee_id=emp.id).first()
    if existing:
        return jsonify({"success": False, "error": f"{emp.full_name} is already assigned"})
    assign = RotationAssignment(
        rotation_id=rot.id,
        employee_id=emp.id,
        current_week=int(data.get("current_week", 0)),
        start_date=_dt.datetime.strptime(data["start_date"], "%Y-%m-%d").date()
            if data.get("start_date") else None,
    )
    db.session.add(assign)
    db.session.commit()
    return jsonify({"success": True, "rotation": rot.to_dict()})


@settings_bp.route("/customization/rotations/unassign", methods=["POST"])
@admin_required
def unassign_rotation():
    dg = _demo_guard()
    if dg:
        return dg
    """Remove an employee from a rotation pattern."""
    from app.models import db, RotationAssignment, RotationPattern
    data = request.get_json(silent=True) or {}
    assign = RotationAssignment.query.get(data.get("id"))
    if not assign:
        return jsonify({"success": False, "error": "Assignment not found"})
    rot_id = assign.rotation_id
    db.session.delete(assign)
    db.session.commit()
    rot = RotationPattern.query.get(rot_id)
    return jsonify({"success": True, "rotation": rot.to_dict() if rot else {}})


# ── Fill-In Rules CRUD ─────────────────────────────────────

@settings_bp.route("/customization/fill-in-rules/list", methods=["GET", "POST"])
@admin_required
def list_fill_in_rules():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_settings_data
        demo = get_demo_settings_data()
        return jsonify({"success": True, "rules": demo["fill_in_rules"]})
    from app.models import FillInRule
    rules = FillInRule.query.order_by(FillInRule.shift_category, FillInRule.priority).all()
    return jsonify({"success": True, "rules": [r.to_dict() for r in rules]})


@settings_bp.route("/customization/fill-in-rules/save", methods=["POST"])
@admin_required
def save_fill_in_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, FillInRule
    data = request.get_json(silent=True) or {}
    rule_id = data.get("id")
    if rule_id:
        rule = FillInRule.query.get(rule_id)
        if not rule:
            return jsonify({"success": False, "error": "Rule not found"})
    else:
        rule = FillInRule()
        db.session.add(rule)

    rule.shift_category = data.get("shift_category", rule.shift_category or "closing")
    rule.employee_id = int(data["employee_id"]) if data.get("employee_id") else rule.employee_id
    rule.priority = int(data.get("priority", rule.priority or 0))
    rule.planning_unit_id = int(data["planning_unit_id"]) if data.get("planning_unit_id") else None
    rule.fallback_template_id = int(data["fallback_template_id"]) if data.get("fallback_template_id") else None
    rule.is_active = data.get("is_active", True)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        err = str(e)
        if "uq_fillin_cat_employee" in err:
            return jsonify({"success": False, "error": "This employee already has a rule for that shift category."})
        return jsonify({"success": False, "error": err})

    return jsonify({"success": True, "rule": rule.to_dict()})


@settings_bp.route("/customization/fill-in-rules/delete", methods=["POST"])
@admin_required
def delete_fill_in_rule():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, FillInRule
    data = request.get_json(silent=True) or {}
    rule = FillInRule.query.get(data.get("id"))
    if not rule:
        return jsonify({"success": False, "error": "Rule not found"})
    db.session.delete(rule)
    db.session.commit()
    return jsonify({"success": True})


# ── Rotation Schedule Generation ────────────────────────────

@settings_bp.route("/customization/rotations/generate", methods=["POST"])
@admin_required
def generate_rotation_schedules():
    dg = _demo_guard()
    if dg:
        return dg
    """
    Generate schedule rows from rotation patterns for a date range.
    POST JSON: {rotation_id?, start_date, end_date}
    If rotation_id is omitted, generates for ALL active rotations.
    """
    from app.models import (db, RotationPattern, RotationAssignment, Employee,
                            Schedule, ShiftSegment, ShiftTemplate, PlanningUnit)
    import json as _json

    data = request.get_json(silent=True) or {}
    start_str = data.get("start_date", "")
    end_str = data.get("end_date", "")
    if not start_str or not end_str:
        return jsonify({"success": False, "error": "Start and end dates are required"})

    start_date = _dt.datetime.strptime(start_str, "%Y-%m-%d").date()
    end_date = _dt.datetime.strptime(end_str, "%Y-%m-%d").date()
    if end_date < start_date:
        return jsonify({"success": False, "error": "End date must be after start date"})
    if (end_date - start_date).days > 90:
        return jsonify({"success": False, "error": "Max 90 days per generation"})

    rot_id = data.get("rotation_id")
    if rot_id:
        rotations = [RotationPattern.query.get(rot_id)]
        rotations = [r for r in rotations if r]
    else:
        rotations = RotationPattern.query.filter_by(is_active=True).all()

    if not rotations:
        return jsonify({"success": False, "error": "No rotation patterns found"})

    # Cache shift templates by id
    templates = {t.id: t for t in ShiftTemplate.query.all()}

    DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    created = 0
    skipped = 0
    warnings = []
    skip_notes = {}   # reason -> count (rolled up so the list stays readable)

    def _note(reason):
        skip_notes[reason] = skip_notes.get(reason, 0) + 1

    def _parse_hhmm(s):
        parts = str(s).strip().split(":")
        return _dt.time(int(parts[0]), int(parts[1]))

    def _to_min(s):
        t = _parse_hhmm(s)
        return t.hour * 60 + t.minute

    def _to_hhmm(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    # Availability (PTO + accommodations + per-day availability) via the
    # scheduling engine so rotations honour the same rules as Generate.
    from app.scheduling.engine import _get_availability, _explain_unavailability, _generate_segments
    try:
        from app.people.manager import get_availability_map
        avail_map = get_availability_map()
    except Exception as e:
        avail_map = {}
        warnings.append(f"Could not load availability/accommodations: {e}")

    # Per-day stagger counters so breaks/lunches are spread across staff
    per_day_index = {}

    def _build_schedule(emp, d, tmpl, status):
        """Create a Schedule (+ segments) for emp on date d from template tmpl,
        adjusted for the employee's accommodation. Returns (sched, reason)."""
        avail = _get_availability(emp.full_name, d, avail_map,
                                  employee_ext_id=emp.employee_id)
        if not avail["available"]:
            return None, _explain_unavailability(emp.full_name, d, avail_map, emp.employee_id)

        start_min = _to_min(tmpl.start_time)
        end_min = _to_min(tmpl.end_time)
        shift_type = tmpl.shift_type or "full"

        # Accommodation: fixed shift start / end override the template
        adjusted = False
        if avail.get("shift_start"):
            start_min = _to_min(avail["shift_start"]); adjusted = True
        if avail.get("shift_end"):
            end_min = _to_min(avail["shift_end"]); adjusted = True
        if avail.get("earliest_start") and start_min < _to_min(avail["earliest_start"]):
            start_min = _to_min(avail["earliest_start"]); adjusted = True
        if avail.get("latest_end") and end_min > _to_min(avail["latest_end"]):
            end_min = _to_min(avail["latest_end"]); adjusted = True
        if avail["day_type"] == "half":
            shift_type = "half"
            half = (end_min - start_min) // 2
            end_min = start_min + half
            adjusted = True
        if end_min <= start_min:
            return None, "accommodation window leaves no shift time"

        hours = round((end_min - start_min) / 60, 2)
        pu_id = tmpl.planning_unit_id if getattr(tmpl, "planning_unit_id", None) else emp.planning_unit_id

        sched = Schedule(
            employee_id=emp.id,
            planning_unit_id=pu_id,
            schedule_date=d,
            shift_start=_dt.time(start_min // 60, start_min % 60),
            shift_end=_dt.time(end_min // 60, end_min % 60),
            shift_type=shift_type,
            hours=hours,
            status=status,
        )
        db.session.add(sched)
        db.session.flush()

        # Segments: use the template's own segments when the shift is unchanged,
        # otherwise generate break/lunch placement from the segment-code rules.
        segs = []
        if not adjusted and tmpl.segments_json:
            try:
                segs = _json.loads(tmpl.segments_json) or []
            except Exception:
                segs = []
        if not segs:
            idx = per_day_index.get(d, 0)
            per_day_index[d] = idx + 1
            segs = _generate_segments(_to_hhmm(start_min), _to_hhmm(end_min), shift_type,
                                      stagger_index=idx, total_employees=max(1, idx + 1))
        for i, seg in enumerate(segs):
            if not seg.get("start") or not seg.get("end"):
                continue
            db.session.add(ShiftSegment(
                schedule_id=sched.id,
                activity_type=seg.get("type", "on-call"),
                start_time=_parse_hhmm(seg["start"]),
                end_time=_parse_hhmm(seg["end"]),
                duration_mins=int(seg.get("duration_mins", 0)),
                sort_order=i,
                notes=seg.get("notes", ""),
            ))
        return sched, None

    covered = set()  # (date, shift_category) covered by rotation schedules
    total_assignments = 0

    for rot in rotations:
        weeks = _json.loads(rot.weeks_json) if rot.weeks_json else []
        if not weeks:
            warnings.append(f"Rotation '{rot.name}' has no weeks defined — skipped.")
            continue
        cycle_len = len(weeks)
        if not rot.assignments:
            warnings.append(f"Rotation '{rot.name}' has no employees assigned.")
            continue

        for assign in rot.assignments:
            emp = assign.employee
            if not emp:
                _note("assignment has no employee")
                continue
            if str(emp.status or "Active").strip().lower() not in ("active", ""):
                _note(f"{emp.full_name} is {emp.status}")
                continue
            total_assignments += 1

            d = start_date
            while d <= end_date:
                anchor = assign.start_date or start_date
                days_since = (d - anchor).days
                if days_since < 0:
                    _note(f"{emp.full_name}: date before rotation start ({anchor})")
                    d += _dt.timedelta(days=1)
                    continue
                week_num = ((days_since // 7) + (assign.current_week or 0)) % cycle_len
                shifts_map = weeks[week_num].get("shifts", {})
                template_id = shifts_map.get(DAY_KEYS[d.weekday()])

                if not template_id or str(template_id).lower() in ("off", ""):
                    d += _dt.timedelta(days=1)
                    continue
                try:
                    tid = int(template_id)
                except (ValueError, TypeError):
                    _note(f"invalid template id '{template_id}'")
                    d += _dt.timedelta(days=1)
                    continue
                tmpl = templates.get(tid)
                if not tmpl:
                    warnings.append(f"Shift template {tid} not found for {emp.full_name} on {d}")
                    d += _dt.timedelta(days=1)
                    continue

                existing = Schedule.query.filter_by(employee_id=emp.id, schedule_date=d).first()
                if existing:
                    skipped += 1
                    covered.add((d, tmpl.shift_category or "any"))
                    d += _dt.timedelta(days=1)
                    continue

                sched, reason = _build_schedule(emp, d, tmpl, "scheduled")
                if sched:
                    created += 1
                    covered.add((d, tmpl.shift_category or "any"))
                else:
                    _note(f"{emp.full_name}: {reason}")
                d += _dt.timedelta(days=1)

    if total_assignments == 0:
        warnings.append("No active employees are assigned to any rotation — nothing to generate.")

    db.session.commit()

    # ── FILL-IN PASS ──────────────────────────────────────────
    # After base rotation schedules are built, detect days where a shift
    # category has no coverage (the rotation-assigned person is on PTO or
    # has no shift that day) and auto-fill from the FillInRule priority list.
    from app.models import FillInRule, PTOEntry

    fill_in_created = 0
    fill_rules = FillInRule.query.filter_by(is_active=True).order_by(FillInRule.priority).all()

    if fill_rules:
        # Group rules by shift_category
        rules_by_cat = {}
        for rule in fill_rules:
            rules_by_cat.setdefault(rule.shift_category, []).append(rule)

        # Build a set of PTO dates per employee for quick lookup
        pto_entries = PTOEntry.query.filter(
            PTOEntry.start_date <= end_date,
            PTOEntry.end_date >= start_date,
        ).all()
        pto_dates = {}  # {employee_id: set of dates}
        for pto in pto_entries:
            s = set()
            d = max(pto.start_date, start_date)
            while d <= min(pto.end_date, end_date):
                s.add(d)
                d += _dt.timedelta(days=1)
            pto_dates.setdefault(pto.employee_id, set()).update(s)

        # For each shift category that has fill-in rules, check each day
        for cat, rules in rules_by_cat.items():
            # Find all shift templates in this category
            cat_templates = [t for t in templates.values()
                            if (t.shift_category or "any") == cat and t.is_active]
            if not cat_templates:
                continue

            DAY_TYPES = ["weekday"] * 5 + ["saturday", "sunday"]
            d = start_date
            while d <= end_date:
                day_name = DAY_TYPES[d.weekday()]
                # Only fill days this category actually operates on
                day_templates = [ct for ct in cat_templates
                                 if (ct.day_type or "any") in ("any", day_name)]
                if not day_templates:
                    d += _dt.timedelta(days=1)
                    continue

                has_coverage = (d, cat) in covered
                if not has_coverage:
                    # Also check schedules already in the DB (from earlier runs)
                    for es in Schedule.query.filter_by(schedule_date=d).all():
                        for ct in cat_templates:
                            if (es.shift_start and es.shift_end and
                                    es.shift_start.strftime("%H:%M") == ct.start_time and
                                    es.shift_end.strftime("%H:%M") == ct.end_time):
                                has_coverage = True
                                break
                        if has_coverage:
                            break

                if not has_coverage:
                    for rule in rules:
                        emp = rule.employee
                        if not emp or str(emp.status or "Active").strip().lower() not in ("active", ""):
                            continue
                        if emp.id in pto_dates and d in pto_dates[emp.id]:
                            continue
                        if Schedule.query.filter_by(employee_id=emp.id, schedule_date=d).first():
                            continue

                        tmpl = templates.get(rule.fallback_template_id) if rule.fallback_template_id else None
                        if not tmpl:
                            tmpl = day_templates[0]

                        sched, reason = _build_schedule(emp, d, tmpl, "fill-in")
                        if not sched:
                            _note(f"fill-in {emp.full_name}: {reason}")
                            continue
                        fill_in_created += 1
                        covered.add((d, cat))
                        warnings.append(f"Fill-in: {emp.full_name} covers {cat} on {d}")
                        break  # filled, move to next day

                d += _dt.timedelta(days=1)

        db.session.commit()

    # Roll up skip reasons into readable warnings
    for reason, count in sorted(skip_notes.items(), key=lambda x: -x[1]):
        warnings.insert(0, f"Skipped {count} day(s) — {reason}")

    msg = f"Created {created} schedule(s), skipped {skipped} (already existed)."
    if fill_in_created:
        msg += f" Auto-filled {fill_in_created} gap(s)."

    return jsonify({
        "success": True,
        "created": created + fill_in_created,
        "skipped": skipped,
        "fill_ins": fill_in_created,
        "warnings": warnings,
        "message": msg,
    })


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

    g.name = name
    g.description = (data.get("description") or "").strip()
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


# ── Availability ─────────────────────────────────────────────
@settings_bp.route("/customization/availability/save", methods=["POST"])
@admin_required
def save_availability():
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import EmployeeAvailability, db
    data = request.get_json(force=True)
    emp_id = data.get("employee_id")
    day = data.get("day_of_week")
    if emp_id is None or day is None:
        return jsonify(success=False, error="employee_id and day_of_week required"), 400

    row = EmployeeAvailability.query.filter_by(employee_id=emp_id, day_of_week=day).first()
    if not row:
        row = EmployeeAvailability(employee_id=emp_id, day_of_week=day)
        db.session.add(row)

    row.is_available = data.get("is_available", True)

    def _parse_time(val):
        if not val:
            return None
        from datetime import time as _time
        parts = val.split(":")
        return _time(int(parts[0]), int(parts[1]))

    row.earliest_start = _parse_time(data.get("earliest_start"))
    row.latest_start = _parse_time(data.get("latest_start"))
    row.latest_end = _parse_time(data.get("latest_end"))
    row.notes = (data.get("notes") or "").strip()[:255]
    db.session.commit()
    return jsonify(success=True, row=row.to_dict())
