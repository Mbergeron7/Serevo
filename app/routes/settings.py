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
from app.auth import login_required, get_current_user
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
@login_required
def index():
    return render_template("settings/index.html",
        user=get_current_user(),
    )


@settings_bp.route("/api-connections")
@login_required
def api_connections():
    from app.models import APIConnection
    connections = APIConnection.query.order_by(APIConnection.created_at.desc()).all()
    return render_template("settings/api_connections.html",
        user=get_current_user(),
        connections=connections,
        providers=PROVIDERS,
    )


@settings_bp.route("/api-connections/save", methods=["POST"])
@login_required
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
@login_required
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
@login_required
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
@login_required
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
