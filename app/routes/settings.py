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


def _clean_token(raw):
    """Strip whitespace/newlines from a stored token. Some tokens look
    base64-encoded but must be used as-is (not decoded)."""
    return (raw or "").strip().replace("\n", "").replace("\r", "")



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




def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(ok=True, demo=True, message="Changes are not saved in demo mode.")
    return None



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




# Register sub-module routes (must be at bottom to avoid circular imports)
from app.routes import settings_general  # noqa: F401,E402
from app.routes import settings_customization  # noqa: F401,E402
from app.routes import settings_scheduling  # noqa: F401,E402
from app.routes import settings_config  # noqa: F401,E402
