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


@settings_bp.route("/google-sheets/test", methods=["POST"])
@admin_required
def test_google_sheets():
    from app.models import db, AppSetting
    from app.data_source import _open_capacity_sheet

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
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Serevo/1.0")

        if conn.auth_type == "bearer":
            token = creds.get("access_token") or creds.get("api_key", "")
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
    """Main customization page with tabs for segments, shifts, rotations, LOB settings."""
    from app.models import SegmentCode, ShiftTemplate, RotationPattern, LOBSetting, PlanningUnit
    user = get_current_user()

    segments = [s.to_dict() for s in SegmentCode.query.order_by(SegmentCode.sort_order, SegmentCode.label).all()]
    shifts = [s.to_dict() for s in ShiftTemplate.query.order_by(ShiftTemplate.sort_order, ShiftTemplate.name).all()]
    rotations = [r.to_dict() for r in RotationPattern.query.order_by(RotationPattern.name).all()]

    lob_settings = [s.to_dict() for s in LOBSetting.query.all()]
    all_lobs = [{"id": pu.id, "name": pu.name} for pu in PlanningUnit.query.order_by(PlanningUnit.name).all()]

    return render_template("settings/customization.html",
        user=user,
        segments=segments,
        shifts=shifts,
        rotations=rotations,
        lob_settings=lob_settings,
        all_lobs=all_lobs,
    )


# ── Segment Codes CRUD ───────────────────────────────────────

@settings_bp.route("/customization/segments/save", methods=["POST"])
@admin_required
def save_segment():
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
    else:
        if SegmentCode.query.filter_by(code=code).first():
            return jsonify({"success": False, "error": f"Code '{code}' already exists"})
        seg = SegmentCode(
            code=code, label=label,
            color=data.get("color", "#6b7280"),
            is_productive=bool(data.get("is_productive", True)),
            is_paid=bool(data.get("is_paid", True)),
            sort_order=int(data.get("sort_order", 0)),
        )
        db.session.add(seg)
    db.session.commit()
    return jsonify({"success": True, "segment": seg.to_dict()})


@settings_bp.route("/customization/segments/delete", methods=["POST"])
@admin_required
def delete_segment():
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
    from app.models import db, ShiftTemplate
    data = request.get_json(silent=True) or {}
    shift_id = data.get("id")
    name = (data.get("name") or "").strip()
    start_time = (data.get("start_time") or "").strip()
    end_time = (data.get("end_time") or "").strip()
    if not name or not start_time or not end_time:
        return jsonify({"success": False, "error": "Name, start time, and end time are required"})

    segments_json = json.dumps(data.get("segments", []))

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
    else:
        shift = ShiftTemplate(
            name=name, start_time=start_time, end_time=end_time,
            hours=float(data.get("hours", 8.0)),
            shift_type=data.get("shift_type", "full"),
            segments_json=segments_json,
            sort_order=int(data.get("sort_order", 0)),
        )
        db.session.add(shift)
    db.session.commit()
    return jsonify({"success": True, "shift": shift.to_dict()})


@settings_bp.route("/customization/shifts/delete", methods=["POST"])
@admin_required
def delete_shift():
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
    from app.models import db, RotationPattern
    data = request.get_json(silent=True) or {}
    rot = RotationPattern.query.get(data.get("id"))
    if not rot:
        return jsonify({"success": False, "error": "Rotation pattern not found"})
    db.session.delete(rot)
    db.session.commit()
    return jsonify({"success": True})


# ── LOB Settings CRUD ────────────────────────────────────────

@settings_bp.route("/customization/lob-settings/save", methods=["POST"])
@admin_required
def save_lob_setting():
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
    for field in ["operating_start", "operating_end"]:
        if field in data:
            setattr(setting, field, data[field])

    db.session.commit()
    return jsonify({"success": True, "setting": setting.to_dict()})


@settings_bp.route("/customization/lob-settings/delete", methods=["POST"])
@admin_required
def delete_lob_setting():
    from app.models import db, LOBSetting
    data = request.get_json(silent=True) or {}
    setting = LOBSetting.query.get(data.get("id"))
    if not setting:
        return jsonify({"success": False, "error": "LOB setting not found"})
    db.session.delete(setting)
    db.session.commit()
    return jsonify({"success": True})
