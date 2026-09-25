# wfm_ticket_portal.py
# Full consolidated Flask app — all fixes applied, service level endpoints added

import re
import os
import csv
import io
import gc
import json as _json
import logging
import tempfile
from datetime import datetime, timedelta, date as _date
from zoneinfo import ZoneInfo
from collections import defaultdict, Counter

# Logging must be set up before anything else uses it
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wfm_app")

from flask import (
    Flask, render_template, request, redirect, session,
    Response, jsonify, send_from_directory, url_for
)
from werkzeug.utils import secure_filename
from markupsafe import Markup
try:
    import schedule_builder
except ImportError:
    schedule_builder = None
import capacity_planner as cp
try:
    import capacity_export as ce
except ImportError:
    ce = None

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========== Single parse_date definition ==========
def parse_date(value, as_date=False):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.date() if as_date else dt
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(value.strip())
        return dt.date() if as_date else dt
    except Exception:
        return None

def parse_date_safe(value):
    return parse_date(value, as_date=True)

# ========== Configuration ==========
SHEET_KEY            = os.environ.get("SHEET_KEY", "1gzJ30wmAAcwEJ8H_nte7ZgH6suYZjGX_w86BhPIRndU")
LEGACY_SHEET_KEY     = os.environ.get("LEGACY_SHEET_KEY", "1JMxIdmpNyQ8vjh_8nHO5WKUX2y4egeL0K5GoEPSp238")
SERVICE_ACCOUNT_FILE = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")
SECRET_KEY           = os.environ.get("WFM_SECRET_KEY", "dev-secret-change-me")
DEBUG_MODE           = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
TIMEZONE             = os.environ.get("WFM_TZ", "America/Toronto")

if SECRET_KEY == "dev-secret-change-me" and not DEBUG_MODE:
    import warnings
    warnings.warn(
        "WFM_SECRET_KEY is not set — using insecure default. "
        "Set the WFM_SECRET_KEY environment variable before deploying.",
        stacklevel=1
    )

# ========== Flask app ==========
import os
app = Flask(__name__,
    template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates'),
    static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
    static_url_path='/static'
)
app.secret_key = SECRET_KEY
app.config['DEBUG'] = DEBUG_MODE

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ── Backup data store ─────────────────────────────────────────────────────────
# Holds manually-uploaded CSV data as a fallback when Google Sheets / CP is unavailable.
# Keyed by date string (YYYY-MM-DD). Cleared on new upload or new day.
import tempfile as _tempfile
import pickle as _pickle

_BACKUP_PERSIST_FILE = os.path.join(_tempfile.gettempdir(), 'wfm_backup_calls.pkl')

def _save_backup_to_disk():
    """Persist backup call data to disk so it survives worker restarts."""
    try:
        with open(_BACKUP_PERSIST_FILE, 'wb') as f:
            _pickle.dump((_BACKUP_CALL_ROWS, _BACKUP_CALL_DATE), f)
    except Exception as e:
        log.warning(f"Could not persist backup to disk: {e}")

def _load_backup_from_disk():
    """Reload backup call data from disk on worker start."""
    global _BACKUP_CALL_ROWS, _BACKUP_CALL_DATE
    try:
        if os.path.exists(_BACKUP_PERSIST_FILE):
            with open(_BACKUP_PERSIST_FILE, 'rb') as f:
                rows, date = _pickle.load(f)
            if rows and date:
                _BACKUP_CALL_ROWS = rows
                _BACKUP_CALL_DATE = date
                log.info(f"Restored backup call data from disk: {date}, {len(rows)} dates")
    except Exception as e:
        log.warning(f"Could not load backup from disk: {e}")

# Restore on startup — must run after _BACKUP_CALL_ROWS and _BACKUP_CALL_DATE are declared below
_BACKUP_AGENT_ROWS  = {}   # date → list of row dicts (agent status SUMMARY — First Login/Log Off)
_BACKUP_AGENT_EVENTS_ROWS = {}   # date → list of row dicts (FULL granular agent status — same as live sheet)
_BACKUP_AGENT_EVENTS_DATE = None

# ── AHOD (All Hands on Deck) contingency plan — SS Sales ──────────────────
# Static reference plan. Shared server-side ack store so any Team Lead
# checking a box is visible to everyone, not just their own browser.
_AHOD_ACKS = {}   # date_str → { level_int: {"checked": bool, "by": str, "at": str} }

AHOD_LEVELS = [
    {"level": 1,  "tier": "yellow", "action": "Reduce absenteeism, improve PAW",                       "owner": "Team Leaders",  "default_status": "Active"},
    {"level": 2,  "tier": "yellow", "action": "Manage adherence and OOQ to target",                    "owner": "Team Leaders",  "default_status": "Active"},
    {"level": 3,  "tier": "yellow", "action": "MBWA — management by walking around",                   "owner": "Team Leaders",  "default_status": "Active"},
    {"level": 4,  "tier": "yellow", "action": "TLs on floor full-time, manage adherence + AHT",         "owner": "Team Leaders",  "default_status": "Active"},
    {"level": 5,  "tier": "yellow", "action": "Pause all offline activity except Sr. Mgr–approved",     "owner": "WFM",           "default_status": "Active"},
    {"level": 6,  "tier": "red",    "action": "Request OT, coordinate with WFM",                        "owner": "Team Leaders",  "default_status": "Active"},
    {"level": 7,  "tier": "red",    "action": "Reduce cool-down to 15 seconds",                          "owner": "WFM",           "default_status": "Active"},
    {"level": 8,  "tier": "red",    "action": "Trainer / Onboarding prime(s) / Store Liaison take calls","owner": "Senior Manager","default_status": "Active"},
    {"level": 9,  "tier": "red",    "action": "Availability gate — QA Sales take calls",                 "owner": "Senior Manager","default_status": "Active"},
    {"level": 10, "tier": "red",    "action": "Availability gate — QA Order Quality take calls",         "owner": "Senior Manager","default_status": "Active"},
    {"level": 11, "tier": "red",    "action": "Availability gate — e-Chat take calls",                   "owner": "Senior Manager","default_status": "Standby"},
    {"level": 12, "tier": "red",    "action": "Gradual reassignment of Web Lead advisors",                "owner": "Senior Manager","default_status": "Standby"},
    {"level": 13, "tier": "red",    "action": "Team Leaders take calls (last resort)",                    "owner": "Senior Manager","default_status": "Standby"},
]
AHOD_LOB = "SS Sales"
_BACKUP_CALL_ROWS   = {}   # date → list of row dicts (same schema as CP call volume sheet)
_BACKUP_CALL_DATE   = None # date the call backup is for
_BACKUP_AGENT_DATE  = None # date the agent backup is for

# Restore persisted backup data from disk (survives worker restarts)
_load_backup_from_disk()

def _get_agent_events_backup(date_str):
    """Return the raw agent-events backup rows if active for the given date, else None."""
    if _BACKUP_AGENT_EVENTS_DATE == date_str and _BACKUP_AGENT_EVENTS_ROWS.get(_BACKUP_AGENT_EVENTS_DATE):
        return _BACKUP_AGENT_EVENTS_ROWS[_BACKUP_AGENT_EVENTS_DATE]
    return None


# ── WFM Email Auto-Ticket Poller ─────────────────────────────────────────────
# Polls rc.wfm@storagevaultcanada.com for new WFM form confirmation emails
# and creates tickets automatically. Uses your existing delegated M365 access
# via a user access token — NO Azure app registration or admin access needed.
#
# How it works:
#   1. You visit /auto_ticket/auth in your browser (while logged into M365)
#   2. That stores your access token temporarily in the app
#   3. Background thread polls the mailbox every 5 minutes using that token
#   4. Parses "WFM - {LOB} - {Request} - {Advisor} - {Date}" subject format
#   5. Creates a ticket in Google Sheets for each new email
#
# Token must be refreshed after expiry (~1hr) by visiting /auto_ticket/auth again
# OR you can set MS_USER_TOKEN env var in Render for a longer-lived token.

# ── WFM Email Auto-Ticket Poller ─────────────────────────────────────────────
# Polls rc.wfm@storagevaultcanada.com for new WFM form confirmation emails
# and creates tickets automatically.
#
# Uses Microsoft's public Graph Explorer client ID — no Azure app registration
# or admin access needed. Refresh tokens last 90 days.
#
# Setup (one-time):
#   1. Visit /auto_ticket/auth and follow the device code flow
#   2. Copy the MS_REFRESH_TOKEN value shown and add it to Render env vars
#   3. Done — runs permanently, silently renewing the access token every hour
#
# Required Render env var (after first setup):
#   MS_REFRESH_TOKEN — obtained via /auto_ticket/auth device code flow

WFM_MAILBOX         = "rc.wfm@storagevaultcanada.com"
MS_POLL_INTERVAL    = 300   # seconds between polls

# Microsoft's public client ID (used by Graph Explorer — no app registration needed)
MS_PUBLIC_CLIENT_ID = "de8bc8b5-d9f9-48b1-a8ad-b748da725064"
MS_TENANT_ID        = "common"  # works for any org account
MS_SCOPES           = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadShared offline_access"

# Token state — kept in memory, refreshed automatically
_ms_access_token    = ""
_ms_refresh_token   = os.environ.get("MS_REFRESH_TOKEN", "")
_ms_token_expiry    = 0.0   # epoch seconds
_ms_seen_email_ids  = set()
_ms_poller_thread   = None
_ms_token_lock      = __import__('threading').Lock()

def _ms_refresh_access_token():
    """Use refresh token to get a new access token. Returns True on success."""
    import urllib.request, urllib.parse, json as _json, time
    global _ms_access_token, _ms_refresh_token, _ms_token_expiry
    with _ms_token_lock:
        rt = _ms_refresh_token
    if not rt:
        return False
    url  = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "client_id":     MS_PUBLIC_CLIENT_ID,
        "refresh_token": rt,
        "scope":         MS_SCOPES,
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=15) as resp:
            body = _json.loads(resp.read())
        with _ms_token_lock:
            _ms_access_token   = body["access_token"]
            _ms_token_expiry   = time.time() + int(body.get("expires_in", 3600)) - 60
            # Refresh token may be rotated — update if new one provided
            if body.get("refresh_token"):
                _ms_refresh_token = body["refresh_token"]
        log.info("MS access token refreshed successfully")
        return True
    except Exception as e:
        log.warning(f"MS token refresh failed: {e}")
        return False

def _ms_get_token():
    """Return a valid access token, refreshing automatically if needed."""
    import time
    with _ms_token_lock:
        token   = _ms_access_token
        expiry  = _ms_token_expiry
    if token and time.time() < expiry:
        return token
    # Token missing or expired — refresh it
    if _ms_refresh_access_token():
        with _ms_token_lock:
            return _ms_access_token
    return None

def _ms_set_tokens(access_token, refresh_token):
    """Store both tokens after initial device code auth."""
    import time
    global _ms_access_token, _ms_refresh_token, _ms_token_expiry
    with _ms_token_lock:
        _ms_access_token  = access_token
        _ms_refresh_token = refresh_token
        _ms_token_expiry  = time.time() + 3500  # ~1 hour

def _ms_graph_get(path):
    """Make a GET request to Microsoft Graph."""
    import urllib.request, json as _json
    token = _ms_get_token()
    if not token:
        return None
    url = f"https://graph.microsoft.com/v1.0{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        log.warning(f"MS Graph GET {path} failed: {e}")
        return None

def _ms_device_code_start():
    """Start device code flow. Returns {user_code, device_code, verification_uri, expires_in}."""
    import urllib.request, urllib.parse, json as _json
    url  = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/devicecode"
    data = urllib.parse.urlencode({
        "client_id": MS_PUBLIC_CLIENT_ID,
        "scope":     MS_SCOPES,
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=15) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        log.error(f"Device code start failed: {e}")
        return None

def _ms_device_code_poll(device_code):
    """Poll for token after user completes device code auth. Returns token dict or None."""
    import urllib.request, urllib.parse, json as _json
    url  = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    data = urllib.parse.urlencode({
        "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
        "client_id":   MS_PUBLIC_CLIENT_ID,
        "device_code": device_code,
    }).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=data), timeout=15) as resp:
            return _json.loads(resp.read())
    except Exception as e:
        body = getattr(e, 'read', lambda: b'{}')()
        try:
            import json as _json2
            return _json2.loads(body)
        except Exception:
            return {"error": str(e)}



def _ms_parse_wfm_email(subject, body, sender, received):
    """
    Parse a WFM form confirmation email into ticket fields.
    Subject format: WFM - {LOB} - {Request Type} - {Advisor} - {Date}
    Returns dict of ticket fields, or None if not a WFM form email.
    """
    import re
    # Strip reply/forward prefixes from subject
    subject_clean = re.sub(r'^(RE|FW|FWD)\s*:\s*', '', (subject or "").strip(), flags=re.IGNORECASE).strip()

    # Match structured subject: WFM - {LOB} - {Request Type} - {Advisor} - {Date}
    m = re.match(
        r'^WFM\s*-\s*(.+?)\s*-\s*(.+?)\s*-\s*(.+?)\s*-\s*(\d{4}-\d{2}-\d{2})\s*$',
        subject_clean
    )
    if m:
        lob          = m.group(1).strip()
        request_type = m.group(2).strip()
        advisor      = m.group(3).strip()
        req_date     = m.group(4).strip()
    else:
        # Freeform email
        lob          = ""
        request_type = subject_clean or "Email Request"
        advisor      = ""
        req_date     = received[:10] if received else ""

    # Extract team lead from body (TL: or Team Lead: line)
    team_lead = ""
    tl_match = re.search(
        r'^(?:TL|Team Lead)\s*:\s*(.+)$', body or "", re.MULTILINE | re.IGNORECASE)
    if tl_match:
        team_lead = tl_match.group(1).strip()

    # Clean body for notes — strip email signature block
    notes_body = (body or "").strip()
    # Strip signature — look for common signature markers (case-insensitive)
    sig_patterns = [
        r'\nMike Bergeron\b',
        r'\nrc\.wfm@',
        r'\nmBergeron@',
        r'\[cid:image',
        r'\nStorageVaultCanada\.com',
    ]
    for pat in sig_patterns:
        m2 = re.search(pat, notes_body, re.IGNORECASE)
        if m2 and m2.start() > 0:
            notes_body = notes_body[:m2.start()].strip()
            break
    # If nothing left after stripping, keep the original body (better than nothing)
    if not notes_body:
        notes_body = (body or "").strip()

    return {
        "lob":          lob,
        "wfm_request":  request_type,
        "advisor_name": advisor,
        "request_date": req_date,
        "team_lead":    team_lead,
        "Notes":        notes_body,
        "Submitted By": sender or WFM_MAILBOX,
    }

def _ms_fetch_new_emails():
    """Fetch unprocessed WFM emails from rc.wfm mailbox."""
    # Search sent items for WFM - pattern (these are the form confirmations)
    data = _ms_graph_get(
        f"/users/{WFM_MAILBOX}/mailFolders/SentItems/messages"
        f"?$filter=startswith(subject,'WFM -')"
        f"&$orderby=sentDateTime desc"
        f"&$top=25"
        f"&$select=id,subject,bodyPreview,body,from,sentDateTime,toRecipients"
    )
    emails = []
    if data and "value" in data:
        emails.extend(data["value"])

    # Also check inbox for any plain emails sent TO rc.wfm (not from the form)
    inbox = _ms_graph_get(
        f"/users/{WFM_MAILBOX}/mailFolders/Inbox/messages"
        f"?$orderby=receivedDateTime desc"
        f"&$top=25"
        f"&$select=id,subject,bodyPreview,body,from,receivedDateTime,toRecipients"
    )
    if inbox and "value" in inbox:
        emails.extend(inbox["value"])

    return emails

def _ms_poll_once():
    """Check for new emails and create tickets for unprocessed ones."""
    global _ms_seen_email_ids
    if not _ms_get_token():
        return

    emails = _ms_fetch_new_emails()
    new_count = 0

    for email in emails:
        msg_id = email.get("id", "")
        if not msg_id or msg_id in _ms_seen_email_ids:
            continue

        subject  = email.get("subject", "")
        body     = (email.get("body", {}) or {}).get("content", "") or email.get("bodyPreview", "")
        sender   = (email.get("from", {}) or {}).get("emailAddress", {}).get("address", "")
        received = email.get("sentDateTime") or email.get("receivedDateTime", "")

        # Skip non-WFM emails from inbox
        if "mailFolders/Inbox" not in email.get("@odata.context", "") and \
           not subject.startswith("WFM"):
            _ms_seen_email_ids.add(msg_id)
            continue

        parsed = _ms_parse_wfm_email(subject, body, sender, received)
        if parsed and _ms_create_ticket(parsed):
            _ms_seen_email_ids.add(msg_id)
            new_count += 1

    if new_count:
        log.info(f"Auto-ticket: created {new_count} ticket(s) from WFM emails")

def _ms_create_ticket(fields):
    """Write a ticket row to Google Sheets."""
    global sheet
    if sheet is None:
        return False
    try:
        all_values = sheet.get_all_values()
        headers    = all_values[0] if all_values else []
        if not headers:
            return False

        tz        = ZoneInfo(TIMEZONE)
        now       = datetime.now(tz)
        ts        = now.strftime("%Y-%m-%d %H:%M:%S")
        ts_id     = now.strftime("%Y%m%d%H%M%S")
        ticket_id = f"TXT-{ts_id}"

        form_data = {
            "ticket_id":    ticket_id,
            "Submitted At": ts,
            "Submitted By": fields.get("Submitted By", WFM_MAILBOX),
            "Status":       "Open",
            "advisor_name": fields.get("advisor_name", ""),
            "team_lead":    fields.get("team_lead", ""),
            "wfm_request":  fields.get("wfm_request", ""),
            "request_date": fields.get("request_date", ts[:10]),
            "Notes":        fields.get("Notes", fields.get("notes", "")),
        }
        row = [form_data.get(h, "") for h in headers]
        sheet.append_row(row)
        _invalidate_cache()
        log.info(f"Auto-ticket {ticket_id}: {form_data['advisor_name']} — {form_data['wfm_request']}")
        return True
    except Exception as e:
        log.error(f"Auto-ticket creation failed: {e}")
        return False

def _ms_start_poller():
    """Start background polling thread."""
    import threading, time
    global _ms_poller_thread

    def _init():
        """Mark existing emails as seen so we don't import history."""
        global _ms_seen_email_ids
        emails = _ms_fetch_new_emails()
        for e in emails:
            mid = e.get("id", "")
            if mid:
                _ms_seen_email_ids.add(mid)
        log.info(f"Auto-ticket poller: marked {len(_ms_seen_email_ids)} existing emails as seen")

    def _run():
        _init()
        while True:
            time.sleep(MS_POLL_INTERVAL)
            try:
                _ms_poll_once()
            except Exception as e:
                log.error(f"Auto-ticket poller error: {e}")

    _ms_poller_thread = threading.Thread(target=_run, daemon=True, name="wfm-email-poller")
    _ms_poller_thread.start()
    log.info("WFM email auto-ticket poller started")



# ========== Security configuration ==========
app.config.update(
    SESSION_COOKIE_HTTPONLY  = True,      # JS cannot read session cookie
    SESSION_COOKIE_SAMESITE  = 'Lax',     # CSRF protection
    SESSION_COOKIE_SECURE    = not DEBUG_MODE,  # HTTPS only in production
    PERMANENT_SESSION_LIFETIME = __import__('datetime').timedelta(hours=8),
    MAX_CONTENT_LENGTH       = 16 * 1024 * 1024,  # 16 MB upload limit
)

# ── Security headers on every response ───────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options']        = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection']       = '1; mode=block'
    response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']     = 'geolocation=(), microphone=(), camera=()'
    if not DEBUG_MODE:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    # Inject dark mode CSS + toggle button into every HTML page
    if (response.content_type and 'text/html' in response.content_type
            and not response.direct_passthrough):
        try:
            dark_snippet = (
                '\n<link rel="stylesheet" href="/static/dark.css">'
                '\n<button id="dark-toggle" onclick="toggleDark()" title="Toggle dark mode"'
                ' style="position:fixed;bottom:20px;right:20px;width:40px;height:40px;'
                'border-radius:50%;border:none;background:#1a237e;color:#fff;font-size:1.1rem;'
                'cursor:pointer;z-index:9999;box-shadow:0 3px 12px rgba(0,0,0,.3)">&#127769;</button>'
                '\n<script>'
                '\n(function(){'
                '\n  if(localStorage.getItem("wfm_dark")==="1")document.body.classList.add("dark");'
                '\n  var b=document.getElementById("dark-toggle");'
                '\n  if(b)b.innerHTML=document.body.classList.contains("dark")?"&#9728;":"&#127769;";'
                '\n})();'
                '\nfunction toggleDark(){'
                '\n  var d=document.body.classList.toggle("dark");'
                '\n  localStorage.setItem("wfm_dark",d?"1":"0");'
                '\n  var b=document.getElementById("dark-toggle");'
                '\n  if(b)b.innerHTML=d?"&#9728;":"&#127769;";'
                '\n}'
                '\n</script>'
            ).encode('utf-8')
            data = response.get_data(as_text=False)
            if data and b'</body>' in data:
                data = data.replace(b'</body>', dark_snippet + b'</body>', 1)
                response.set_data(data)
            elif data and b'</html>' in data:
                data = data.replace(b'</html>', dark_snippet + b'</html>', 1)
                response.set_data(data)
        except Exception as e:
            log.debug(f"Dark mode injection skipped: {e}")

    return response

# ── Login rate limiter ────────────────────────────────────────────────────────
# Tracks failed login attempts per IP. Blocks after 10 failures in 15 minutes.
import time as _time
_login_attempts  = {}   # ip → [timestamp, ...]
_LOGIN_MAX       = 10   # max attempts
_LOGIN_WINDOW    = 900  # 15 minutes in seconds

def _check_rate_limit(ip):
    """Return True if IP is rate-limited (too many recent failures)."""
    now   = _time.time()
    times = [t for t in _login_attempts.get(ip, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[ip] = times
    return len(times) >= _LOGIN_MAX

def _record_failed_login(ip):
    now = _time.time()
    _login_attempts.setdefault(ip, []).append(now)
    log.warning(f"Failed login attempt from {ip} ({len(_login_attempts[ip])} in window)")

def _clear_login_attempts(ip):
    _login_attempts.pop(ip, None)

# ── OTP store ─────────────────────────────────────────────────────────────────
# One-time codes sent to users on login. 10-minute expiry.
import secrets as _secrets
_otp_store = {}   # email → {code, expires}
_OTP_TTL   = 600  # 10 minutes

def _generate_otp(email):
    code = str(_secrets.randbelow(900000) + 100000)  # 6-digit
    _otp_store[email.lower()] = {
        'code':    code,
        'expires': _time.time() + _OTP_TTL,
    }
    log.info(f"OTP generated for {email}")
    return code

def _verify_otp(email, code):
    entry = _otp_store.get(email.lower())
    if not entry:
        return False
    if _time.time() > entry['expires']:
        del _otp_store[email.lower()]
        return False
    if entry['code'] != code.strip():
        return False
    del _otp_store[email.lower()]  # one-time use
    return True

def _send_otp_email(email, code):
    """
    Send OTP code via SendGrid HTTPS API (works on Render free tier).
    Set Render env var: SENDGRID_API_KEY
    Sender email must be verified in SendGrid dashboard.
    Free tier: 100 emails/day — plenty for login codes.
    """
    import urllib.request, json as _json, threading

    api_key   = os.environ.get("SENDGRID_API_KEY", "")
    from_email = os.environ.get("SENDGRID_FROM", os.environ.get("SMTP_USER", "svi.rcwfm@gmail.com"))

    if not api_key:
        log.warning(f"SENDGRID_API_KEY not set — OTP for {email}: {code}")
        return False

    def _send():
        try:
            payload = {
                "personalizations": [{"to": [{"email": email}]}],
                "from":    {"email": from_email, "name": "WFM Portal"},
                "subject": f"WFM Portal Login Code: {code}",
                "content": [
                    {
                        "type":  "text/plain",
                        "value": (
                            f"Your WFM Portal login code is:\n\n"
                            f"    {code}\n\n"
                            f"This code expires in 10 minutes.\n"
                            f"If you did not request this, ignore this email."
                        )
                    }
                ]
            }
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=_json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info(f"OTP email sent to {email} (status {resp.status})")
        except Exception as e:
            log.error(f"OTP email failed for {email}: {e}")

    threading.Thread(target=_send, daemon=True, name="otp-mailer").start()
    return True


scope  = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
client = main_doc = sheet = log_sheet = recycle_sheet = archive_sheet = checklist_sheet = None

try:
    creds    = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    client   = gspread.authorize(creds)
    main_doc = client.open_by_key(SHEET_KEY)
    sheet    = main_doc.sheet1

    try:
        log_sheet = main_doc.worksheet("Update Log")
    except Exception:
        log.info("Update Log worksheet not found — log writes will be skipped.")
        log_sheet = None

    try:
        recycle_sheet = main_doc.worksheet("Recycle Bin")
        log.info("Recycle Bin worksheet loaded successfully.")
    except Exception as e:
        log.warning(f"Recycle Bin worksheet not found: {e}")
        recycle_sheet = None

    try:
        archive_sheet = main_doc.worksheet("Archive")
        log.info("Archive worksheet loaded successfully.")
    except Exception as e:
        log.warning(f"Archive worksheet not found — will create on first use: {e}")
        archive_sheet = None

    # ChecklistState — shared daily WFM checklist state + AHOD/VTO activity
    try:
        checklist_sheet = main_doc.worksheet("ChecklistState")
        log.info("ChecklistState worksheet loaded.")
    except Exception:
        try:
            checklist_sheet = main_doc.add_worksheet("ChecklistState", rows=200, cols=9)
            checklist_sheet.update("A1:G1", [["date", "kind", "item_id",
                                              "task", "value", "who", "updated_at"]])
            log.info("ChecklistState worksheet created.")
        except Exception as e:
            log.warning(f"Could not create ChecklistState: {e}")
            checklist_sheet = None

    log.info("Google Sheets authorized.")
except Exception:
    log.exception("Google Sheets setup failed. Some features will be unavailable.")

# Start the WFM email poller if a refresh token is configured
if _ms_refresh_token:
    _ms_start_poller()
else:
    log.info("WFM email poller not started — visit /auto_ticket/auth to set up")

# ========== Users ==========
ALLOWED_USERS = [
    'mbergeron@storagevaultcanada.com',
    'jsauve@storagevaultcanada.com',
    'ddevenny@storagevaultcanada.com',
    'mikebergeron36@gmail.com',
    'szenasni@storagevaultcanada.com',
    'csteffi@storagevaultcanada.com',
]
ADMIN_USERS = [
    "mikebergeron36@gmail.com",
    "mbergeron@storagevaultcanada.com",
]

# Display names for assignment dropdown — email → full name
STAFF_NAMES = {
    'mbergeron@storagevaultcanada.com': 'Michael Bergeron',
    'jsauve@storagevaultcanada.com':    'Joshua Sauve',
    'ddevenny@storagevaultcanada.com':  'Dawn Devenny',
    'mikebergeron36@gmail.com':         'Michael Bergeron',
    'szenasni@storagevaultcanada.com':  'Sara Zenasni',
    'csteffi@storagevaultcanada.com':   'Claudis Steffi',
}
# Sorted unique staff list for dropdowns (deduplicated by name)
ASSIGNABLE_STAFF = sorted(set(STAFF_NAMES.values()))

# ========== Sheet cache ==========
_sheet_cache: dict = {"data": None, "ts": 0}
_CACHE_TTL = 120

def _invalidate_cache():
    _sheet_cache["data"]            = None
    _sheet_cache["ts"]              = 0
    _sheet_cache["live"]            = None
    _sheet_cache["live_ts"]         = 0
    _sheet_cache["with_archive"]    = None
    _sheet_cache["with_archive_ts"] = 0

def get_sheet_values_safe(include_archive=False):
    if not sheet:
        return [], []
    now       = datetime.now().timestamp()
    cache_key = "with_archive" if include_archive else "live"
    if (_sheet_cache.get(cache_key) is not None
            and (now - _sheet_cache.get(cache_key + "_ts", 0)) < _CACHE_TTL):
        return _sheet_cache[cache_key]
    try:
        all_rows = sheet.get_all_values()
        if not all_rows:
            return [], []
        headers = normalize_headers(all_rows[0])
        rows    = all_rows[1:]
        if include_archive and archive_sheet:
            try:
                arch_rows = archive_sheet.get_all_values()
                if arch_rows and len(arch_rows) > 1:
                    rows = rows + arch_rows[1:]
            except Exception:
                log.warning("Archive merge failed — serving live rows only", exc_info=True)
        result = (headers, rows)
        _sheet_cache[cache_key]          = result
        _sheet_cache[cache_key + "_ts"]  = now
        if not include_archive:
            _sheet_cache["data"] = result
            _sheet_cache["ts"]   = now
        return result
    except Exception:
        log.exception("Error reading sheet")
        return [], []

# ========== Helpers ==========
def _norm_key(s):
    """Normalize a header/field name for tolerant matching:
    trim, lowercase, collapse whitespace to underscores."""
    return re.sub(r"\s+", "_", (s or "").strip().lower())

def normalize_headers(headers):
    out = []
    for h in headers:
        hh  = (h or "").strip()
        key = hh.lower().replace(" ", "_")
        if key in ("closed_date", "closedat", "closed"):
            out.append("Closed At")
        elif key in ("submitted_at", "submittedat", "submitted"):
            out.append("Submitted At")
        elif key in ("closed_by",):
            out.append("Closed By")
        elif key in ("ticket_id", "ticketid"):
            out.append("ticket_id")
        else:
            out.append(hh)
    return out

def safe_append_row(ws, row_data):
    """Append a row after the last row that contains data.
    Uses gspread's append_row which finds the first empty row correctly,
    rather than relying on ws.row_count (which returns the grid size, not
    the data row count, causing writes to land past all existing data)."""
    ws.append_row(row_data, value_input_option="RAW", insert_data_option="INSERT_ROWS")

def find_row_index_by_ticket_id(all_rows, headers, ticket_id, ticket_col_name="ticket_id"):
    if not ticket_id:
        return None, None
    ticket_id = ticket_id.strip()
    try:
        col_idx = next(i for i, h in enumerate(headers)
                       if (h or "").strip().lower() == ticket_col_name.lower())
    except StopIteration:
        return None, None
    for r_idx, row in enumerate(all_rows[1:], start=2):
        val = row[col_idx] if len(row) > col_idx else ""
        if (val or "").strip() == ticket_id:
            return r_idx, row
    return None, None

def find_row_index_any_cell(all_rows, ticket_id):
    """Fallback lookup: scan every cell of every data row for an exact
    ticket_id match. Handles rows appended with a column order that
    doesn't match the sheet's header row (e.g. older Recycle Bin rows)."""
    if not ticket_id:
        return None, None
    ticket_id = ticket_id.strip()
    for r_idx, row in enumerate(all_rows[1:], start=2):
        for cell in row:
            if (str(cell) or "").strip() == ticket_id:
                return r_idx, row
    return None, None

def calculate_duration(submitted, closed):
    if not submitted or not closed:
        return ""
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d")
    for fmt in fmts:
        try:
            hours = round(
                (datetime.strptime(closed, fmt) - datetime.strptime(submitted, fmt))
                .total_seconds() / 3600, 1)
            return f"{hours} hrs"
        except Exception:
            continue
    try:
        hours = round(
            (datetime.fromisoformat(closed) - datetime.fromisoformat(submitted))
            .total_seconds() / 3600, 1)
        return f"{hours} hrs"
    except Exception:
        return ""

def log_update(ticket_id, field, value, user):
    if not log_sheet:
        return
    try:
        timestamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        log_sheet.append_row([timestamp, ticket_id, field, value, user])
    except Exception:
        log.exception("Failed to append to log sheet.")

def parse_filter_dates():
    frm = (request.args.get('from') or "").strip()
    to  = (request.args.get('to')   or "").strip()
    from_date = to_date = None
    try:
        if frm: from_date = datetime.strptime(frm, "%Y-%m-%d").date()
        if to:  to_date   = datetime.strptime(to,  "%Y-%m-%d").date()
    except Exception:
        from_date = to_date = None
    return from_date, to_date

def get_legacy_data():
    doc = client.open_by_key(LEGACY_SHEET_KEY)
    ws  = doc.worksheet("Tasks")
    records = ws.get_all_records()
    # Deduplicate — sheet sometimes has rows imported twice
    seen = set()
    deduped = []
    for r in records:
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped

def get_legacy_rows():
    doc  = client.open_by_key(LEGACY_SHEET_KEY)
    ws   = doc.worksheet("Tasks")
    data = ws.get_all_values()
    return (data[0] if data else []), (data[1:] if len(data) > 1 else [])

# Portal operating hours (EST). Outside these hours the portal signs
# everyone out so Render's free tier spins down from inactivity.
PORTAL_OPEN_HOUR  = int(os.environ.get("PORTAL_OPEN_HOUR",  "6"))   # 6:00 AM
PORTAL_CLOSE_HOUR = int(os.environ.get("PORTAL_CLOSE_HOUR", "23"))  # 11:00 PM

def _portal_is_open():
    """Return True if current EST time is within operating hours."""
    now_h = datetime.now(ZoneInfo(TIMEZONE)).hour
    return PORTAL_OPEN_HOUR <= now_h < PORTAL_CLOSE_HOUR

def _require_auth():
    if not session.get('authenticated'):
        return redirect('/login')
    # Outside operating hours — sign the user out so all browser tabs
    # stop sending keepalive requests and Render's free tier can spin down.
    if not _portal_is_open():
        session.clear()
        return redirect('/login?msg=closed')
    return None

def _require_admin():
    return session.get('user_email') not in ADMIN_USERS

# ========== Schedule store ==========
_schedule_store: dict = {}

# ========== AUTH ==========
@app.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

    if request.method == 'POST':
        if _check_rate_limit(ip):
            return render_template('login.html',
                error="Too many failed attempts. Please wait 15 minutes.")

        email    = (request.form.get('email')    or '').strip().lower()
        password = (request.form.get('password') or '').strip()

        if not email or not password:
            return render_template('login.html', error="Please enter your email and password.")

        allowed = [u.lower() for u in ALLOWED_USERS + ADMIN_USERS]
        portal_password = os.environ.get("PORTAL_PASSWORD", "")

        # Constant-time comparison to prevent timing attacks
        email_ok    = email in allowed
        password_ok = portal_password and _secrets.compare_digest(password, portal_password)

        if email_ok and password_ok:
            _clear_login_attempts(ip)
            session.clear()
            session['authenticated'] = True
            session['user_email']    = email
            session.permanent        = True
            log.info(f"Successful login: {email} from {ip}")
            return redirect('/landing')
        else:
            _record_failed_login(ip)
            log.warning(f"Failed login for '{email}' from {ip}")
            return render_template('login.html', error="Invalid email or password.")

    msg = request.args.get('msg', '')
    if msg == 'closed':
        open_time = f"{PORTAL_OPEN_HOUR}:00 AM" if PORTAL_OPEN_HOUR < 12 else f"{PORTAL_OPEN_HOUR-12}:00 PM"
        return render_template('login.html',
            error=f"The portal is closed overnight and will reopen at {open_time} EST. "
                  f"Render will restart automatically when the first user logs in.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    sid = session.get('_id')
    if sid and sid in _schedule_store:
        del _schedule_store[sid]
    session.clear()
    return redirect('/login')

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    auth = _require_auth()
    if auth: return auth
    # Prevent path traversal
    import posixpath
    filename = posixpath.normpath(filename)
    if filename.startswith('..') or '/' in filename:
        return "Invalid path", 400
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@app.route('/rtm/backup', methods=['GET'])
def rtm_backup():
    auth = _require_auth()
    if auth: return auth
    global _BACKUP_CALL_DATE, _BACKUP_AGENT_DATE, _BACKUP_AGENT_EVENTS_DATE
    return render_template('rtm_backup.html',
        call_date=_BACKUP_CALL_DATE,
        agent_date=_BACKUP_AGENT_DATE,
        agent_events_date=_BACKUP_AGENT_EVENTS_DATE,
        agent_events_rows=len(_BACKUP_AGENT_EVENTS_ROWS.get(_BACKUP_AGENT_EVENTS_DATE, [])) if _BACKUP_AGENT_EVENTS_DATE else 0,
        call_rows=len(_BACKUP_CALL_ROWS.get(_BACKUP_CALL_DATE, [])) if _BACKUP_CALL_DATE else 0,
        agent_rows=len(_BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE, [])) if _BACKUP_AGENT_DATE else 0,
    )

@app.route('/rtm/backup/upload-calls', methods=['POST'])
def rtm_backup_upload_calls():
    """Accept a manually-downloaded CP call volume CSV and store it in memory.
    Supports multi-day track_callcenter_tasks files — rows are grouped by
    their EST date so the interval report can display any uploaded day."""
    auth = _require_auth()
    if auth: return auth
    global _BACKUP_CALL_ROWS, _BACKUP_CALL_DATE

    f = request.files.get('file')
    if not f or not f.filename.endswith('.csv'):
        return jsonify({"ok": False, "error": "Please upload a .csv file"}), 400

    try:
        content = f.read().decode('utf-8-sig')
        reader  = csv.DictReader(io.StringIO(content))
        all_rows = list(reader)
        if not all_rows:
            return jsonify({"ok": False, "error": "File is empty"}), 400

        # Filter to calls only (is_task=0) — exclude task rows
        rows = []
        for r in all_rows:
            is_task = str(r.get("is_task", "0") or "0").strip()
            if is_task == "1":
                continue
            # CSV has no duration column — add placeholder (AHT won't show but calls count)
            if "duration" not in r:
                r["duration"] = "0"
            rows.append(r)

        if not rows:
            return jsonify({"ok": False, "error": "No call rows found (all rows were tasks)"}), 400

        # Check per-row wait-time coverage
        qt_present = sum(1 for r in rows if str(r.get("queue_time", "")).strip() != "")
        qt_coverage_pct = round(qt_present / len(rows) * 100) if rows else 0

        # Group rows by EST date (date_created is CST, +1h for EST)
        by_date = {}
        for r in rows:
            dc = str(r.get("date_created", "") or r.get("Date Created", "")).strip()
            if not dc:
                continue
            dt_cst = _rtm._parse_datetime_str(dc) if _rtm else None
            if dt_cst:
                dt_est = _rtm._cp_to_est(dt_cst)
                d_str = dt_est.strftime("%Y-%m-%d")
            else:
                d_str = dc[:10]
            by_date.setdefault(d_str, []).append(r)

        if not by_date:
            # fallback: use today
            today_str = _rtm._today_est().strftime("%Y-%m-%d") if _rtm else datetime.now().strftime("%Y-%m-%d")
            by_date[today_str] = rows

        dates_sorted = sorted(by_date.keys())
        _BACKUP_CALL_ROWS = by_date
        _BACKUP_CALL_DATE = dates_sorted[-1]  # default to most recent date
        _save_backup_to_disk()

        warning = None
        if qt_coverage_pct == 0:
            warning = ("None of these rows have a queue_time value — Service Level, "
                       "ASA, and Max Queued will show as unavailable (—) rather than "
                       "a misleading number. Call counts (Offered/Answered/Abandoned) "
                       "are still accurate.")
        elif qt_coverage_pct < 80:
            warning = (f"Only {qt_coverage_pct}% of rows have a queue_time value. "
                       f"Service Level is calculated only from calls with known wait "
                       f"time, so it may be based on a smaller sample than the full "
                       f"call count for some intervals.")

        date_summary = ", ".join(f"{d} ({len(by_date[d])} rows)" for d in dates_sorted)
        return jsonify({"ok": True, "rows": len(rows),
                        "date": _BACKUP_CALL_DATE,
                        "dates": dates_sorted,
                        "date_summary": date_summary,
                        "warning": warning})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/rtm/backup/upload-agents', methods=['POST'])
def rtm_backup_upload_agents():
    """Accept a manually-downloaded agent status CSV and store it in memory."""
    auth = _require_auth()
    if auth: return auth
    global _BACKUP_AGENT_ROWS, _BACKUP_AGENT_DATE

    f = request.files.get('file')
    if not f or not f.filename.endswith('.csv'):
        return jsonify({"ok": False, "error": "Please upload a .csv file"}), 400

    try:
        content = f.read().decode('utf-8-sig')
        reader  = csv.DictReader(io.StringIO(content))
        rows    = list(reader)
        if not rows:
            return jsonify({"ok": False, "error": "File is empty"}), 400

        # Try to detect date from First Login column
        date_str = None
        for r in rows:
            login = r.get("First Login", "")
            if login:
                try:
                    date_str = login[:10]
                    # Normalise MM/DD/YYYY → YYYY-MM-DD
                    if '/' in date_str:
                        parts = date_str.split('/')
                        date_str = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    break
                except Exception:
                    pass

        if not date_str:
            date_str = _rtm._today_est().strftime("%Y-%m-%d") if _rtm else datetime.now().strftime("%Y-%m-%d")

        _BACKUP_AGENT_ROWS = {date_str: rows}
        _BACKUP_AGENT_DATE = date_str
        return jsonify({"ok": True, "rows": len(rows), "date": date_str})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/rtm/backup/upload-agent-events', methods=['POST'])
def rtm_backup_upload_agent_events():
    """
    Accept a manually-pulled FULL Agent Status export — the raw Call
    Potential export (id, user_id, start_time, activity_sid). The live
    Google Sheet stores this same data but renames activity_sid to
    c_activity_sid on the way in — we normalize that here so every
    downstream function (cascades, status-under-5, attendance) can treat
    a manual upload identically to the live sheet without needing to
    know which source it came from.
    """
    auth = _require_auth()
    if auth: return auth
    global _BACKUP_AGENT_EVENTS_ROWS, _BACKUP_AGENT_EVENTS_DATE

    f = request.files.get('file')
    if not f or not f.filename.endswith('.csv'):
        return jsonify({"ok": False, "error": "Please upload a .csv file"}), 400

    try:
        content = f.read().decode('utf-8-sig')
        reader  = csv.DictReader(io.StringIO(content))
        rows    = list(reader)
        if not rows:
            return jsonify({"ok": False, "error": "File is empty"}), 400

        actual_cols = set(rows[0].keys())

        # Normalize activity_sid → c_activity_sid (raw export uses no prefix,
        # live sheet uses c_ prefix — every downstream function expects c_)
        if "activity_sid" in actual_cols and "c_activity_sid" not in actual_cols:
            for r in rows:
                r["c_activity_sid"] = r.get("activity_sid", "")
            actual_cols.add("c_activity_sid")

        required_cols = {"user_id", "start_time", "c_activity_sid"}
        missing       = required_cols - actual_cols
        if missing:
            return jsonify({"ok": False,
                "error": f"Missing expected column(s): {', '.join(sorted(missing))}. "
                         f"This upload needs the raw Call Potential export "
                         f"(id, user_id, start_time, activity_sid), not the "
                         f"summary format."}), 400

        # Detect date from start_time column (most recent date present)
        dates_found = set()
        for r in rows:
            st = str(r.get("start_time", "") or "").strip()
            if st and len(st) >= 10:
                dates_found.add(st[:10])
        date_str = max(dates_found) if dates_found else \
                   (_rtm._today_est().strftime("%Y-%m-%d") if _rtm else datetime.now().strftime("%Y-%m-%d"))

        _BACKUP_AGENT_EVENTS_ROWS = {date_str: rows}
        _BACKUP_AGENT_EVENTS_DATE = date_str
        return jsonify({"ok": True, "rows": len(rows), "date": date_str})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/rtm/backup/clear', methods=['POST'])
def rtm_backup_clear():
    """Clear all backup data."""
    auth = _require_auth()
    if auth: return auth
    global _BACKUP_CALL_ROWS, _BACKUP_CALL_DATE, _BACKUP_AGENT_ROWS, _BACKUP_AGENT_DATE
    global _BACKUP_AGENT_EVENTS_ROWS, _BACKUP_AGENT_EVENTS_DATE
    which = request.json.get('which', 'all') if request.is_json else 'all'
    if which in ('calls', 'all'):
        _BACKUP_CALL_ROWS  = {}
        _BACKUP_CALL_DATE  = None
        try:
            if os.path.exists(_BACKUP_PERSIST_FILE):
                os.remove(_BACKUP_PERSIST_FILE)
        except Exception:
            pass
    if which in ('agents', 'all'):
        _BACKUP_AGENT_ROWS = {}
        _BACKUP_AGENT_DATE = None
    if which in ('agent-events', 'all'):
        _BACKUP_AGENT_EVENTS_ROWS = {}
        _BACKUP_AGENT_EVENTS_DATE = None
    return jsonify({"ok": True})

@app.route('/rtm/backup/status')
def rtm_backup_status():
    """Return current backup data status — used by other RTM pages to show banner."""
    auth = _require_auth()
    if auth: return auth
    return jsonify({
        "call_backup_active":  bool(_BACKUP_CALL_DATE),
        "call_backup_date":    _BACKUP_CALL_DATE,
        "call_backup_dates":   sorted(_BACKUP_CALL_ROWS.keys()) if _BACKUP_CALL_ROWS else [],
        "call_backup_rows":    sum(len(v) for v in _BACKUP_CALL_ROWS.values()),
        "agent_backup_active": bool(_BACKUP_AGENT_DATE),
        "agent_backup_date":   _BACKUP_AGENT_DATE,
        "agent_backup_rows":   len(_BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE, [])),
        "agent_events_backup_active": bool(_BACKUP_AGENT_EVENTS_DATE),
        "agent_events_backup_date":   _BACKUP_AGENT_EVENTS_DATE,
        "agent_events_backup_rows":   len(_BACKUP_AGENT_EVENTS_ROWS.get(_BACKUP_AGENT_EVENTS_DATE, [])),
    })

@app.route('/auto_ticket/auth', methods=['GET', 'POST'])
def auto_ticket_auth():
    """Device code flow auth for WFM email poller. Admin only."""
    auth_check = _require_auth()
    if auth_check: return auth_check
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403

    if request.method == 'POST':
        data   = request.get_json(silent=True) or {}
        action = data.get('action', '')
        if action == 'start':
            dc = _ms_device_code_start()
            if not dc or 'error' in dc:
                return jsonify({"ok": False, "error": (dc or {}).get('error_description', 'Failed')}), 500
            return jsonify({"ok": True, "user_code": dc.get("user_code"),
                            "verification_uri": dc.get("verification_uri"),
                            "device_code": dc.get("device_code"),
                            "expires_in": dc.get("expires_in", 900)})
        elif action == 'poll':
            result = _ms_device_code_poll(data.get('device_code', ''))
            if not result:
                return jsonify({"ok": False, "error": "Poll failed"}), 500
            if result.get('error') == 'authorization_pending':
                return jsonify({"ok": False, "pending": True})
            if result.get('error'):
                return jsonify({"ok": False, "error": result.get('error_description', result.get('error'))}), 400
            access_token  = result.get('access_token', '')
            refresh_token = result.get('refresh_token', '')
            if access_token:
                _ms_set_tokens(access_token, refresh_token)
                global _ms_poller_thread
                if _ms_poller_thread is None or not _ms_poller_thread.is_alive():
                    _ms_start_poller()
                return jsonify({"ok": True, "refresh_token": refresh_token,
                                "message": "Authorized! Save the refresh token to Render env vars as MS_REFRESH_TOKEN."})
            return jsonify({"ok": False, "error": "No access token in response"}), 400
        return jsonify({"error": "Unknown action"}), 400

    # GET — device code flow UI
    has_rt = bool(_ms_refresh_token)
    html = """<!doctype html>
<html><head><title>Auto-Ticket Auth</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<style>
body{max-width:760px;margin:40px auto;padding:0 20px;font-family:Segoe UI,Arial,sans-serif}
.code-box{background:#1e1e1e;color:#4fc3f7;font-size:1.8rem;font-weight:700;
           letter-spacing:.2em;padding:18px;border-radius:10px;text-align:center;font-family:monospace}
.token-box{word-break:break-all;font-size:.7rem;background:#f8f9fa;padding:10px;
            border-radius:6px;font-family:monospace;border:1px solid #dee2e6;max-height:80px;overflow:auto}
</style></head><body>
<h4 class="mb-1">WFM Email Auto-Ticket — Authorization</h4>
<p class="text-muted">Permanently authorizes reading of <strong>rc.wfm@storagevaultcanada.com</strong>.
Uses Microsoft device code flow — no admin access or app registration needed.</p>
""" + ('<div class="alert alert-success">✅ Refresh token configured — poller active. Re-authorize below when it expires (~90 days).</div>' if has_rt else '<div class="alert alert-warning">⚠ Not yet authorized. Follow the steps below.</div>') + """
<div class="card mb-3">
  <div class="card-header fw-bold">Step 1 — Generate your sign-in code</div>
  <div class="card-body">
    <p>Click below to get a one-time code, then sign in at Microsoft to authorize access.</p>
    <button id="startBtn" class="btn btn-primary" onclick="startFlow()">Generate Code</button>
    <div id="codeSection" class="mt-3" style="display:none">
      <div class="code-box mb-2" id="userCode"></div>
      <p class="text-center">Go to: <a id="verifyLink" href="#" target="_blank" class="fw-bold fs-5"></a></p>
      <p class="text-muted text-center small">Sign in with <strong>mbergeron@storagevaultcanada.com</strong> and enter the code above</p>
      <p class="text-center"><span class="spinner-border spinner-border-sm text-primary me-2" id="spinner"></span>
      <span id="pollStatus">Waiting for sign-in...</span></p>
    </div>
  </div>
</div>
<div class="card mb-3" id="successCard" style="display:none">
  <div class="card-header fw-bold bg-success text-white">✅ Step 2 — Save your refresh token to Render</div>
  <div class="card-body">
    <p>In Render → your service → <strong>Environment</strong>, add:</p>
    <p><strong>Key:</strong> <code>MS_REFRESH_TOKEN</code><br><strong>Value:</strong> (copy below)</p>
    <div class="token-box mb-2" id="rtDisplay"></div>
    <button class="btn btn-sm btn-outline-secondary" onclick="copyRT()">📋 Copy</button>
    <hr><p class="text-muted small mb-0">After saving in Render, redeploy. Poller starts automatically on every restart.
    Token valid ~90 days — set a calendar reminder to renew.</p>
  </div>
</div>
<div class="card mb-3">
  <div class="card-header fw-bold">Status</div>
  <div class="card-body" id="statusBox">Loading...</div>
</div>
<a href="/landing" class="btn btn-outline-secondary btn-sm">← Home</a>
<script>
var _dc='', _timer=null;
function startFlow(){
  var btn=document.getElementById('startBtn');
  btn.disabled=true; btn.textContent='Generating...';
  fetch('/auto_ticket/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'start'})})
  .then(r=>r.json()).then(d=>{
    if(!d.ok){alert('Error: '+d.error);btn.disabled=false;btn.textContent='Try Again';return;}
    _dc=d.device_code;
    document.getElementById('userCode').textContent=d.user_code;
    var a=document.getElementById('verifyLink');a.href=d.verification_uri;a.textContent=d.verification_uri;
    document.getElementById('codeSection').style.display='block';
    btn.textContent='Waiting for sign-in...';
    _timer=setInterval(pollToken,5000);
  }).catch(e=>{alert('Failed: '+e);btn.disabled=false;btn.textContent='Try Again';});
}
function pollToken(){
  fetch('/auto_ticket/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'poll',device_code:_dc})})
  .then(r=>r.json()).then(d=>{
    if(d.pending)return;
    clearInterval(_timer);
    if(d.ok){
      document.getElementById('pollStatus').textContent='✅ Authorized!';
      document.getElementById('spinner').style.display='none';
      document.getElementById('rtDisplay').textContent=d.refresh_token;
      document.getElementById('successCard').style.display='block';
      loadStatus();
    }else{
      document.getElementById('pollStatus').textContent='❌ '+(d.error||'Failed');
      document.getElementById('startBtn').disabled=false;
      document.getElementById('startBtn').textContent='Try Again';
    }
  });
}
function copyRT(){navigator.clipboard.writeText(document.getElementById('rtDisplay').textContent).then(()=>alert('Copied!'));}
function loadStatus(){
  fetch('/auto_ticket/status').then(r=>r.json()).then(d=>{
    document.getElementById('statusBox').innerHTML=
      '<strong>Token ready:</strong> '+(d.token_set?'✅ Yes':'❌ No')+'<br>'+
      '<strong>Poller running:</strong> '+(d.poller_alive?'✅ Yes (every '+(d.poll_interval/60)+' min)':'❌ No')+'<br>'+
      '<strong>Emails tracked:</strong> '+d.emails_seen+'<br>'+
      '<strong>Refresh token saved:</strong> '+(d.refresh_token_set?'✅ Yes (~90 days)':'⚠ Not yet — add MS_REFRESH_TOKEN to Render')+'<br>'+
      '<strong>Mailbox:</strong> '+d.mailbox;
  }).catch(()=>document.getElementById('statusBox').textContent='Could not load status');
}
loadStatus();
</script></body></html>"""
    return html, 200, {'Content-Type': 'text/html'}


@app.route('/auto_ticket/poll', methods=['POST'])
def auto_ticket_poll():
    """Manually trigger a poll. Admin only."""
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return jsonify({"error": "Admin only"}), 403
    try:
        before = len(_ms_seen_email_ids)
        _ms_poll_once()
        after  = len(_ms_seen_email_ids)
        return jsonify({"ok": True, "new_tickets": after - before})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/auto_ticket/status')
def auto_ticket_status():
    """Return poller status. Admin only."""
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return jsonify({"error": "Admin only"}), 403
    return jsonify({
        "token_set":          bool(_ms_get_token()),
        "refresh_token_set":  bool(_ms_refresh_token),
        "emails_seen":        len(_ms_seen_email_ids),
        "poll_interval":      MS_POLL_INTERVAL,
        "poller_alive":       _ms_poller_thread is not None and _ms_poller_thread.is_alive(),
        "mailbox":            WFM_MAILBOX,
    })

@app.route('/inbox/webhook', methods=['POST'])
def inbox_webhook():
    """
    Receives forwarded WFM emails from Pipedream and auto-creates tickets.
    """
    WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
    if not WEBHOOK_SECRET:
        log.error("WEBHOOK_SECRET not set — rejecting webhook for security")
        return jsonify({"error": "Webhook not configured"}), 503
    incoming = request.headers.get("X-Webhook-Secret", "") or \
               (request.get_json(silent=True) or {}).get("secret", "")
    if not _secrets.compare_digest(incoming, WEBHOOK_SECRET):
        log.warning(f"Webhook auth failed from {request.remote_addr}")
        return jsonify({"error": "Unauthorized"}), 401

    if sheet is None:
        return jsonify({"error": "Sheet not connected"}), 500

    try:
        data = request.get_json(silent=True) or {}

        # Log the full payload so we can see exactly what Pipedream sends
        log.info(f"WEBHOOK PAYLOAD keys: {list(data.keys())}")
        log.info(f"WEBHOOK PAYLOAD: {str(data)[:2000]}")

        # Pipedream sends fields at top level — subject/from/date may be
        # nested under headers or at top level depending on trigger config
        subject = (data.get("subject") or
                   data.get("headers", {}).get("subject") or
                   data.get("headers", {}).get("Subject") or "")

        # body text is at top level as "text"
        raw_body = data.get("text") or data.get("body_text") or ""
        if not raw_body and isinstance(data.get("body"), dict):
            raw_body = data["body"].get("text", "")
        elif not raw_body and isinstance(data.get("body"), str):
            raw_body = data["body"]
        body = raw_body

        # Strip HTML tags if needed
        import re as _re
        if body and '<' in str(body):
            body = _re.sub(r'<[^>]+>', ' ', str(body))
            body = _re.sub(r'\s+', ' ', body).strip()

        sender = data.get("from") or data.get("sender") or ""
        if isinstance(sender, dict):
            sender = sender.get("text", "") or sender.get("address", "")
        elif isinstance(sender, list) and sender:
            sender = sender[0].get("address", "") if isinstance(sender[0], dict) else str(sender[0])

        received = (data.get("date") or data.get("sentDateTime") or
                    data.get("receivedDateTime") or "")

        log.info(f"WEBHOOK parsed — subject='{subject}' body_len={len(str(body))} sender='{sender}'")

        fields = _ms_parse_wfm_email(subject, str(body), str(sender), str(received))
        if _ms_create_ticket(fields):
            log.info(f"Webhook ticket created: {subject}")
            return jsonify({"ok": True, "subject": subject,
                            "parsed": fields})
        return jsonify({"ok": False, "error": "Sheet write failed"}), 500

    except Exception as e:
        log.exception("Webhook error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/ping')
def ping():
    """Lightweight keep-alive endpoint — hit every 10 min to prevent Render cold starts."""
    from flask import jsonify
    import datetime
    return jsonify({"status": "ok", "ts": datetime.datetime.utcnow().isoformat()})

@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    """Full cache clear — ticket sheet cache + all RTM page caches.
    Redirects back to the landing page with a confirmation flag."""
    auth = _require_auth()
    if auth: return auth
    _invalidate_cache()
    try:
        import rtm as _rtm
        _rtm.expire_all_cache()
    except Exception:
        log.exception("RTM cache clear failed")
    return redirect('/landing?cache_cleared=1')

@app.route('/landing')
def landing():
    auth = _require_auth()
    if auth: return auth

    import time as _t
    # Cache the LOA/Accom reminder scan — it read ~4 sheet requests on every
    # landing load, a big contributor to hitting the per-minute read quota.
    global _landing_reminder_cache
    try:
        _lrc = _landing_reminder_cache
    except NameError:
        _lrc = _landing_reminder_cache = {"data": None, "ts": 0}
    if _lrc["data"] is not None and _t.time() - _lrc["ts"] < 180:
        upcoming, past_due = _lrc["data"]
    else:
        upcoming, past_due = _landing_reminders()
        _lrc["data"] = (upcoming, past_due)
        _lrc["ts"]   = _t.time()

    loa_count   = sum(1 for u in upcoming + past_due if u["type"] == "LOA")
    accom_count = sum(1 for u in upcoming + past_due if u["type"] == "Accommodation")

    return render_template('landing.html',
                           user_email=session.get('user_email'),
                           upcoming=upcoming, past_due=past_due,
                           loa_count=loa_count, accom_count=accom_count)

_landing_reminder_cache = {"data": None, "ts": 0}
def _landing_reminders():
    upcoming  = []
    past_due  = []
    try:
        from datetime import date, timedelta
        from people_routes import _get_or_create_tab, _rows_to_dicts, LOA_TAB, ACCOM_TAB, LOA_HEADERS, ACCOM_HEADERS
        doc = main_doc
        if doc:
            today    = date.today()
            deadline = today + timedelta(days=7)

            # LOA — check return_date
            try:
                ws   = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
                rows = _rows_to_dicts(ws)
                for r in rows:
                    if r.get("archived","").lower() == "yes": continue
                    if r.get("status","").lower() != "active": continue
                    rd = r.get("return_date","").strip()
                    if not rd: continue
                    try:
                        dt = date.fromisoformat(rd)
                        days_left = (dt - today).days
                        item = {
                            "type":      "LOA",
                            "name":      r.get("name",""),
                            "lob":       r.get("lob",""),
                            "label":     "Return Date",
                            "date":      rd,
                            "days_left": days_left,
                            "url":       "/people/loa",
                        }
                        if dt < today:
                            past_due.append(item)
                        elif dt <= deadline:
                            upcoming.append(item)
                    except: pass
            except Exception:
                log.exception("Landing deadline section failed")

            # Accommodations — check end_date
            try:
                ws   = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
                rows = _rows_to_dicts(ws)
                for r in rows:
                    if r.get("archived","").lower() == "yes": continue
                    if r.get("status","").lower() != "active": continue
                    ed = r.get("end_date","").strip()
                    if not ed: continue
                    try:
                        dt = date.fromisoformat(ed)
                        days_left = (dt - today).days
                        item = {
                            "type":      "Accommodation",
                            "name":      r.get("name",""),
                            "lob":       r.get("lob",""),
                            "label":     "End Date",
                            "date":      ed,
                            "days_left": days_left,
                            "url":       "/people/accommodations",
                        }
                        if dt < today:
                            past_due.append(item)
                        elif dt <= deadline:
                            upcoming.append(item)
                    except: pass
            except Exception:
                log.exception("Landing deadline section failed")

            upcoming.sort(key=lambda x: x["days_left"])
            past_due.sort(key=lambda x: x["days_left"])
    except Exception as e:
        log.warning(f"Landing upcoming check failed: {e}")

    return upcoming, past_due

# ========== FORM ==========
@app.route('/', methods=['GET', 'POST'])
def home():
    auth = _require_auth()
    if auth: return auth
    if sheet is None:
        return "❌ Google Sheet not connected.", 500
    if request.method == 'POST':
        try:
            form_data    = request.form.to_dict()
            # Normalize Notes field — form inputs are often named lowercase
            # ("notes") while the sheet header is "Notes" (capital N).
            # The full request form also uses per-type textareas
            # (accommodation_notes, training_notes, timeoff_reason, etc.)
            # so we scan for any *_notes / *_reason field with content.
            # Without this, notes silently save as blank with no error.
            if "Notes" not in form_data or not form_data.get("Notes", "").strip():
                found = []
                for k in ("notes", "Note", "note", "comments", "Comments"):
                    if form_data.get(k, "").strip():
                        found.append(form_data[k].strip())
                        break
                if not found:
                    for k, v in form_data.items():
                        kl = k.lower()
                        if (kl.endswith("_notes") or kl.endswith("_reason")) and v.strip():
                            found.append(v.strip())
                if found:
                    form_data["Notes"] = "\n".join(found)
            timestamp    = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
            timestamp_id = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y%m%d%H%M%S")
            form_data["Submitted At"] = timestamp
            form_data["Submitted By"] = session.get('user_email')
            form_data["Status"]       = "Open"
            all_values = sheet.get_all_values()
            headers    = all_values[0] if all_values else []
            if any(h.strip().lower() in ("ticket_id", "ticketid") for h in headers):
                form_data["ticket_id"] = f"TXT-{timestamp_id}"
            for key in request.files:
                file = request.files[key]
                if file and file.filename:
                    safe_name = secure_filename(file.filename)
                    filename  = f"{key}_{timestamp_id}_{safe_name}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                    form_data[key] = f"uploads/{filename}"
            # Save matched by normalized header name so sheet headers like
            # "WFM Work Category" still map to form field "wfm_work_category".
            norm_form = {_norm_key(k): v for k, v in form_data.items()}
            sheet.append_row([form_data.get(h, norm_form.get(_norm_key(h), "")) for h in headers])
            _invalidate_cache()
            ticket_id = form_data.get("ticket_id", "")
            return redirect(f'/dashboard?submitted={ticket_id}')
        except Exception:
            log.exception("Form submission error")
            return "Form submission error", 500
    # Render the same dual-mode template used for editing — it shows the
    # blank "Submit WFM Request" form when edit_mode is False. This keeps
    # the create and edit forms permanently in sync (form.html is retired).
    return render_template('edit_ticket.html', edit_mode=False)

@app.route('/edit_ticket/<ticket_id>', methods=['GET', 'POST'])
def edit_ticket(ticket_id):
    """Open the full WFM request form pre-filled with an existing ticket's data."""
    auth = _require_auth()
    if auth: return auth
    if sheet is None:
        return "❌ Google Sheet not connected.", 500

    headers, rows = get_sheet_values_safe()
    if not headers:
        return "Sheet not available", 500

    # Find the ticket row
    hmap = {h: i for i, h in enumerate(headers)}
    ticket_row = None
    row_index  = None
    for i, row in enumerate(rows, 2):  # 1-indexed, row 1 = headers
        tid_idx = hmap.get("ticket_id", -1)
        if tid_idx >= 0 and tid_idx < len(row) and row[tid_idx].strip() == ticket_id:
            ticket_row = row
            row_index  = i
            break

    if ticket_row is None:
        return f"Ticket {ticket_id} not found", 404

    if request.method == 'POST':
        # Update existing row in place
        try:
            form_data = request.form.to_dict()
            # Normalize Notes field the same way as new-ticket submission —
            # including the per-type *_notes / *_reason textareas.
            if "Notes" not in form_data or not form_data.get("Notes", "").strip():
                found = []
                for k in ("notes", "Note", "note", "comments", "Comments"):
                    if form_data.get(k, "").strip():
                        found.append(form_data[k].strip())
                        break
                if not found:
                    for k, v in form_data.items():
                        kl = k.lower()
                        if (kl.endswith("_notes") or kl.endswith("_reason")) and v.strip():
                            found.append(v.strip())
                if found:
                    form_data["Notes"] = "\n".join(found)
            all_values = sheet.get_all_values()
            actual_headers = all_values[0] if all_values else []
            # Find the actual row index again (may have shifted)
            actual_row_idx = None
            tid_col = next((i for i, h in enumerate(actual_headers)
                           if h.strip().lower() == "ticket_id"), -1)
            if tid_col >= 0:
                for i, row in enumerate(all_values[1:], 2):
                    if tid_col < len(row) and row[tid_col].strip() == ticket_id:
                        actual_row_idx = i
                        break
            if actual_row_idx is None:
                return "Ticket not found for update", 404

            # Build updated row — keep existing values for fields not in form.
            # Match by normalized header name so header formatting differences
            # (capitalization/spaces) don't silently drop submitted values.
            norm_form = {_norm_key(k): v for k, v in form_data.items()}
            existing = all_values[actual_row_idx - 1]
            updated  = []
            for j, h in enumerate(actual_headers):
                nk = _norm_key(h)
                if h in form_data:
                    updated.append(form_data[h])
                elif nk in norm_form:
                    updated.append(norm_form[nk])
                elif j < len(existing):
                    updated.append(existing[j])
                else:
                    updated.append("")

            sheet.update(f"A{actual_row_idx}", [updated])
            _invalidate_cache()
            log_update(ticket_id, "Ticket Updated", "Full edit form submitted",
                       session.get('user_email'))
            return redirect(f'/dashboard?edited={ticket_id}')
        except Exception:
            log.exception("Edit ticket error")
            return "Update error", 500

    # GET — build prefill dict for the template
    prefill = {headers[i]: ticket_row[i] for i in range(min(len(headers), len(ticket_row)))}
    return render_template('edit_ticket.html',
        prefill=prefill,
        ticket_id=ticket_id,
        edit_mode=True,
    )

# ========== DASHBOARD ==========
@app.route('/dashboard')
def dashboard():
    auth = _require_auth()
    if auth: return auth
    if sheet is None:
        return "❌ Google Sheet not connected.", 500
    try:
        headers, rows = get_sheet_values_safe()
        if not headers:
            return render_template('dashboard.html',
                headers=[], rows=[], full_headers=[], full_rows=[],
                log_headers=[], log_rows=[],
                is_admin=session.get('user_email') in ADMIN_USERS,
                is_employee=session.get('user_email') in ALLOWED_USERS)
        key_to_display = {
            "advisor_name": "Advisor Name", "request_date": "Request Date",
            "team_lead":    "Team Lead",    "wfm_request":  "WFM Request",
            "Submitted By": "Submitted By", "Submitted At": "Submitted At",
            "Closed At":    "Closed At",    "Status":       "Status",
            "Assigned To":  "Assigned To",  "Closed By":    "Closed By",
            "ticket_id":    "Ticket ID",
        }
        visible_indexes = [i for i, h in enumerate(headers) if h in key_to_display]
        scoped_headers  = [key_to_display[headers[i]] for i in visible_indexes] + ["Duration", "Actions"]
        scoped_rows = []
        for row in rows:
            new_row = [row[i] if i < len(row) else "" for i in visible_indexes]
            try:
                submitted = row[headers.index("Submitted At")] if "Submitted At" in headers else ""
                closed    = row[headers.index("Closed At")]    if "Closed At"    in headers else ""
                new_row.append(calculate_duration(submitted, closed))
            except Exception:
                new_row.append("")
            scoped_rows.append(new_row)
        log_headers, log_rows = [], []
        # Log sheet fetched lazily — not needed for initial render
        return render_template('dashboard.html',
            headers=scoped_headers, rows=scoped_rows,
            full_headers=[], full_rows=[],
            row_indexes=list(range(len(rows))),
            log_headers=log_headers, log_rows=log_rows,
            assignable_staff=ASSIGNABLE_STAFF,
            is_admin=session.get('user_email') in ADMIN_USERS,
            is_employee=session.get('user_email') in ALLOWED_USERS)
    except Exception as e:
        log.exception("Dashboard error")
        return f"Dashboard error: {str(e)}", 500

# ========== REFRESH ==========
@app.route('/quick_assign', methods=['POST'])
def quick_assign():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if sheet is None: return jsonify({"error": "Sheet not connected"}), 500
    try:
        payload     = request.get_json(silent=True) or {}
        ticket_id   = str(payload.get('ticket_id', '') or '').strip()
        assigned_to = str(payload.get('assigned_to', '') or '').strip()
        if not ticket_id:
            return jsonify({"error": "Missing ticket_id"}), 400
        headers, rows = get_sheet_values_safe()
        if not headers:
            return jsonify({"error": "Sheet empty"}), 500
        tid_idx = headers.index('ticket_id') if 'ticket_id' in headers else -1
        if tid_idx < 0:
            return jsonify({"error": "ticket_id column not found"}), 500
        row_num = None
        for i, row in enumerate(rows, start=2):
            if len(row) > tid_idx and str(row[tid_idx]).strip() == ticket_id:
                row_num = i
                break
        if not row_num:
            return jsonify({"error": "Ticket not found"}), 404
        if "Assigned To" in headers:
            col = headers.index("Assigned To") + 1
            sheet.update_cell(row_num, col, assigned_to)
            user = session.get('user_email', 'unknown')
            log_update(ticket_id, "Assigned To", assigned_to, user)
        _invalidate_cache()
        return jsonify({"success": True})
    except Exception as e:
        log.exception("Quick assign error")
        return jsonify({"error": str(e)}), 500

@app.route('/refresh_tickets')
def refresh_tickets():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if sheet is None: return jsonify({"error": "Sheet not connected"}), 500
    try:
        headers, rows = get_sheet_values_safe()
        if not headers: return jsonify({"headers": [], "rows": []})
        display_order = [
            ("advisor_name", "Advisor Name"), ("request_date", "Request Date"),
            ("team_lead",    "Team Lead"),     ("wfm_request",  "WFM Request"),
            ("Submitted By", "Submitted By"),  ("Submitted At", "Submitted At"),
            ("Closed At",    "Closed At"),     ("Status",       "Status"),
            ("Assigned To",  "Assigned To"),   ("Closed By",    "Closed By"),
            ("ticket_id",    "Ticket ID"),     ("Notes",        "Notes"),
        ]
        hmap            = {h: i for i, h in enumerate(headers)}
        display_headers = [label for _, label in display_order] + ["Duration"]
        scoped_rows     = []
        for row in rows:
            r = [row[hmap[k]] if k in hmap and hmap[k] < len(row) else "" for k, _ in display_order]
            r.append(calculate_duration(
                row[hmap["Submitted At"]] if "Submitted At" in hmap else "",
                row[hmap["Closed At"]]    if "Closed At"    in hmap else ""))
            scoped_rows.append(r)
        return jsonify({"headers": display_headers, "rows": scoped_rows})
    except Exception as e:
        log.exception("Refresh failed")
        return jsonify({"error": str(e)}), 500

@app.route('/api/ticket_details/<ticket_id>')
def api_ticket_details(ticket_id):
    """Full ticket row for the dashboard modal — every sheet column in
    order, so the popup can show all filled-in fields (the ticket table
    itself only carries a 12-column summary)."""
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if sheet is None: return jsonify({"error": "Sheet not connected"}), 500
    try:
        headers, rows = get_sheet_values_safe()
        if not headers: return jsonify({"fields": []})
        hmap = {h: i for i, h in enumerate(headers)}
        tid_idx = hmap.get("ticket_id")
        if tid_idx is None: return jsonify({"fields": []})
        for row in rows:
            if tid_idx < len(row) and (row[tid_idx] or "").strip() == ticket_id.strip():
                fields = [[h, row[i] if i < len(row) else ""]
                          for i, h in enumerate(headers)]
                return jsonify({"fields": fields})
        return jsonify({"fields": []}), 404
    except Exception:
        log.exception("ticket_details error")
        return jsonify({"error": "Lookup failed"}), 500

# ========== DOWNLOADS ==========
@app.route('/download_csv')
def download_csv():
    auth = _require_auth()
    if auth: return auth
    if sheet is None: return "❌ Not connected.", 500
    try:
        output = io.StringIO()
        csv.writer(output).writerows(sheet.get_all_values())
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=tickets.csv"
        return response
    except Exception as e:
        return f"CSV error: {e}", 500

@app.route('/download_xlsx')
def download_xlsx():
    auth = _require_auth()
    if auth: return auth
    if sheet is None: return "❌ Not connected.", 500
    try:
        import openpyxl
        from io import BytesIO
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Tickets"
        for r_idx, row in enumerate(sheet.get_all_values(), 1):
            for c_idx, val in enumerate(row, 1):
                ws.cell(row=r_idx, column=c_idx, value=val)
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return Response(output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=tickets.xlsx"})
    except Exception as e:
        return f"XLSX error: {e}", 500

# ========== UPDATE ==========
@app.route('/update_ticket', methods=['POST'])
def update_ticket():
    auth = _require_auth()
    if auth: return auth
    if sheet is None: return "❌ Not connected.", 500
    try:
        ticket_id   = (request.form.get('ticket_id') or "").strip()
        status      = request.form.get('status')
        assigned_to = request.form.get('assigned_to')
        notes       = request.form.get('notes')
        user        = session.get('user_email')
        if not ticket_id: return "Missing ticket ID", 400
        # Single sheet read (headers derived from the same fetch)
        all_rows        = sheet.get_all_values()
        headers         = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, _ = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None: return "Ticket not found", 404
        ts       = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        updates  = {}
        log_rows = []
        if "Status" in headers and status:
            updates[headers.index("Status") + 1] = status
            log_rows.append([ts, ticket_id, "Status", status, user])
        if "Assigned To" in headers and assigned_to:
            updates[headers.index("Assigned To") + 1] = assigned_to
            log_rows.append([ts, ticket_id, "Assigned To", assigned_to, user])
        if notes is not None:
            if "Notes" in headers:
                updates[headers.index("Notes") + 1] = notes
            log_rows.append([ts, ticket_id, "Notes", notes, user])
        if status == "Closed":
            if "Closed By" in headers:
                updates[headers.index("Closed By") + 1] = user
                log_rows.append([ts, ticket_id, "Closed By", user, user])
            if "Closed At" in headers:
                updates[headers.index("Closed At") + 1] = ts
                log_rows.append([ts, ticket_id, "Closed At", ts, user])
        # One batched write for all cells, one append for all log lines
        if updates:
            from gspread.utils import rowcol_to_a1
            sheet.batch_update([
                {"range": rowcol_to_a1(ticket_index, col), "values": [[val]]}
                for col, val in updates.items()])
        if log_rows and log_sheet:
            try:
                log_sheet.append_rows(log_rows, value_input_option="USER_ENTERED")
            except Exception:
                log.exception("Failed to append update log batch")
        _invalidate_cache()
        return redirect('/dashboard')
    except Exception:
        log.exception("Update error")
        return "Update error", 500

# ========== BULK UPDATE ==========
@app.route('/bulk_update_ticket', methods=['POST'])
def bulk_update_ticket():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        payload     = request.get_json(silent=True) or {}
        ticket_id   = (payload.get('ticket_id')   or "").strip()
        status      = (payload.get('status')      or "").strip()
        assigned_to = (payload.get('assigned_to') or "").strip()
        user        = session.get('user_email')
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        # Single sheet read (headers derived from the same fetch)
        all_rows                 = sheet.get_all_values()
        headers                  = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, ticket_row = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None: return jsonify({"error": "Ticket not found"}), 404
        ts       = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        updates  = {}
        log_rows = []
        if status and "Status" in headers:
            updates[headers.index("Status") + 1] = status
            log_rows.append([ts, ticket_id, "Status", status, user])
        if assigned_to and "Assigned To" in headers:
            updates[headers.index("Assigned To") + 1] = assigned_to
            log_rows.append([ts, ticket_id, "Assigned To", assigned_to, user])
        if status == "Closed":
            if "Closed By" in headers:
                updates[headers.index("Closed By") + 1] = user
            if "Closed At" in headers:
                updates[headers.index("Closed At") + 1] = ts
        if updates:
            from gspread.utils import rowcol_to_a1
            sheet.batch_update([
                {"range": rowcol_to_a1(ticket_index, col), "values": [[val]]}
                for col, val in updates.items()])
        if log_rows and log_sheet:
            try:
                log_sheet.append_rows(log_rows, value_input_option="USER_ENTERED")
            except Exception:
                log.exception("Failed to append bulk update log batch")
        _invalidate_cache()
        return jsonify({"success": True})
    except Exception:
        log.exception("Bulk update error")
        return jsonify({"error": "Bulk update failed"}), 500

# ========== TICKET HISTORY ==========
@app.route('/api/ticket_history/<ticket_id>')
def ticket_history(ticket_id):
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if not log_sheet: return jsonify([])
    try:
        data    = log_sheet.get_all_values()
        if not data: return jsonify([])
        headers = data[0]
        result  = []
        for row in data[1:]:
            entry = dict(zip(headers, row))
            if (entry.get("Ticket ID") or "").strip() == ticket_id.strip():
                result.append(entry)
        return jsonify(result)
    except Exception:
        log.exception("ticket_history error")
        return jsonify([]), 500

# ========== DELETE ==========
@app.route('/delete_ticket', methods=['POST'])
def delete_ticket():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if _require_admin(): return jsonify({"error": "Admins only."}), 403
    if sheet is None: return jsonify({"error": "Not connected."}), 500
    try:
        payload   = request.get_json(silent=True) or {}
        ticket_id = (payload.get('ticket_id') or "").strip()
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        # Single sheet read — headers derived from the same fetch
        all_rows                 = sheet.get_all_values()
        headers                  = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, ticket_row = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None: return jsonify({"error": "Ticket not found"}), 404
        global recycle_sheet
        if not recycle_sheet:
            try:
                recycle_sheet = main_doc.add_worksheet("Recycle Bin", rows="100", cols="30")
                safe_append_row(recycle_sheet, all_rows[0])
            except Exception:
                try:
                    recycle_sheet = main_doc.worksheet("Recycle Bin")
                except Exception:
                    recycle_sheet = None
        if recycle_sheet:
            rb = recycle_sheet.get_all_values()
            if not rb:
                safe_append_row(recycle_sheet, all_rows[0])
            safe_append_row(recycle_sheet, ticket_row)
        sheet.delete_rows(ticket_index)
        _invalidate_cache()
        user = session.get('user_email', 'unknown')
        log_update(ticket_id, "Deleted", "Moved to Recycle Bin", user)
        return jsonify({"success": True})
    except Exception:
        log.exception("Delete error")
        return jsonify({"error": "Delete failed"}), 500

# ========== RESTORE FROM RECYCLE BIN ==========
@app.route('/restore_ticket', methods=['POST'])
def restore_ticket():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if _require_admin(): return jsonify({"error": "Admins only."}), 403
    try:
        payload   = request.get_json(silent=True) or {}
        ticket_id = (payload.get('ticket_id') or "").strip()
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        if not recycle_sheet: return jsonify({"error": "Recycle Bin not available"}), 500
        all_rows                 = recycle_sheet.get_all_values()
        headers                  = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, ticket_row = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None:
            ticket_index, ticket_row = find_row_index_any_cell(all_rows, ticket_id)
        if ticket_index is None: return jsonify({"error": "Ticket not found in recycle bin"}), 404
        safe_append_row(sheet, ticket_row)
        recycle_sheet.delete_rows(ticket_index)
        _invalidate_cache()
        return jsonify({"success": True})
    except Exception:
        log.exception("Restore error")
        return jsonify({"error": "Restore failed"}), 500

# ========== DELETE FROM RECYCLE BIN ==========
@app.route('/delete_recycle_entry', methods=['POST'])
def delete_recycle_entry():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if _require_admin(): return jsonify({"error": "Admins only."}), 403
    try:
        payload   = request.get_json(silent=True) or {}
        ticket_id = (payload.get('ticket_id') or "").strip()
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        if not recycle_sheet: return jsonify({"error": "Recycle Bin not available"}), 500
        all_rows        = recycle_sheet.get_all_values()
        headers         = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, _ = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None:
            ticket_index, _ = find_row_index_any_cell(all_rows, ticket_id)
        if ticket_index is None: return jsonify({"error": "Not found"}), 404
        recycle_sheet.delete_rows(ticket_index)
        return jsonify({"success": True})
    except Exception:
        log.exception("Delete recycle error")
        return jsonify({"error": "Delete failed"}), 500

# ========== ARCHIVE ==========
@app.route('/archive_ticket', methods=['POST'])
def archive_ticket():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if sheet is None: return jsonify({"error": "Not connected."}), 500
    try:
        payload   = request.get_json(silent=True) or {}
        ticket_id = (payload.get('ticket_id') or "").strip()
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        # Single sheet read — headers derived from the same fetch
        all_rows                 = sheet.get_all_values()
        headers                  = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, ticket_row = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None: return jsonify({"error": "Ticket not found"}), 404
        global archive_sheet
        if not archive_sheet:
            try:
                archive_sheet = main_doc.add_worksheet("Archive", rows="1000", cols="30")
                safe_append_row(archive_sheet, all_rows[0])
            except Exception:
                try:
                    archive_sheet = main_doc.worksheet("Archive")
                except Exception:
                    archive_sheet = None
        if archive_sheet:
            safe_append_row(archive_sheet, ticket_row)
        else:
            return jsonify({"error": "Archive unavailable"}), 500
        sheet.delete_rows(ticket_index)
        _invalidate_cache()
        user = session.get('user_email', 'unknown')
        log_update(ticket_id, "Archived", "Moved to Archive", user)
        return jsonify({"success": True})
    except Exception:
        log.exception("Archive error")
        return jsonify({"error": "Archive failed"}), 500

# ========== RESTORE FROM ARCHIVE ==========
@app.route('/restore_from_archive', methods=['POST'])
def restore_from_archive():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if _require_admin(): return jsonify({"error": "Admins only."}), 403
    try:
        payload   = request.get_json(silent=True) or {}
        ticket_id = (payload.get('ticket_id') or "").strip()
        if not ticket_id: return jsonify({"error": "Missing ticket ID"}), 400
        if not archive_sheet: return jsonify({"error": "Archive not available"}), 500
        all_rows                 = archive_sheet.get_all_values()
        headers                  = normalize_headers(all_rows[0]) if all_rows else []
        ticket_index, ticket_row = find_row_index_by_ticket_id(all_rows, headers, ticket_id)
        if ticket_index is None:
            ticket_index, ticket_row = find_row_index_any_cell(all_rows, ticket_id)
        if ticket_index is None: return jsonify({"error": "Ticket not found in archive"}), 404
        safe_append_row(sheet, ticket_row)
        archive_sheet.delete_rows(ticket_index)
        _invalidate_cache()
        return jsonify({"success": True})
    except Exception:
        log.exception("Restore from archive error")
        return jsonify({"error": "Restore failed"}), 500

# ========== ARCHIVE VIEW ==========
@app.route('/archive')
def archive_view():
    auth = _require_auth()
    if auth: return auth
    try:
        if not archive_sheet:
            return render_template('archive.html', headers=[], rows=[], ticket_ids=[],
                is_admin=session.get('user_email') in ADMIN_USERS,
                restore_url=url_for('restore_from_archive'))
        all_rows   = archive_sheet.get_all_values()
        headers    = normalize_headers(all_rows[0]) if all_rows else []
        rows       = all_rows[1:] if len(all_rows) > 1 else []
        ticket_ids = []
        tid_idx    = headers.index("ticket_id") if "ticket_id" in headers else -1
        for row in rows:
            tid = row[tid_idx].strip() if tid_idx >= 0 and tid_idx < len(row) else ""
            if not tid:
                for cell in row:
                    if str(cell).startswith("TXT-"):
                        tid = str(cell).strip()
                        break
            ticket_ids.append(tid)
        return render_template('archive.html',
            headers=headers, rows=rows, ticket_ids=ticket_ids,
            is_admin=session.get('user_email') in ADMIN_USERS,
            restore_url=url_for('restore_from_archive'))
    except Exception:
        log.exception("Archive view error")
        return "Archive view error", 500

# ========== HISTORY ==========
@app.route('/history')
def history():
    auth = _require_auth()
    if auth: return auth
    if log_sheet is None: return "❌ Log sheet not connected.", 500
    try:
        ld          = log_sheet.get_all_values()
        log_headers = ld[0] if ld else []
        log_rows    = ld[1:] if len(ld) > 1 else []
        ticket_id   = request.args.get('ticket_id', '')
        filter_user = request.args.get('user_email', '')
        if ticket_id:
            log_rows = [r for r in log_rows if ticket_id in r]
        if session.get('user_email') not in ADMIN_USERS:
            log_rows = [r for r in log_rows if session.get('user_email') in r]
        elif filter_user:
            log_rows = [r for r in log_rows if filter_user in r]

        # ── Group rows into actions ─────────────────────────────────
        # One action (e.g. closing a ticket) writes several log rows in
        # the same second: Status, Closed By, Closed At, Notes. Group by
        # (timestamp, ticket_id, user) so each action is a single line,
        # with the individual steps available behind an Open button.
        def _summarize(steps):
            for s in steps:
                if (s["field"] or "").strip().lower() == "status":
                    v = (s["value"] or "").strip()
                    if "closed" in v.lower(): return "Closed ticket"
                    if v: return f"Status → {v}"
            if len(steps) == 1:
                val = (steps[0]["value"] or "").strip()
                if len(val) > 45: val = val[:45] + "…"
                return f"{steps[0]['field']} → {val}"
            fields = [s["field"] for s in steps]
            return "Updated: " + ", ".join(fields[:4]) + ("…" if len(fields) > 4 else "")

        grouped, order = {}, []
        for r in log_rows:
            ts, tid, field, value, user = (list(r) + ["", "", "", "", ""])[:5]
            key = (ts, tid, user)
            if key not in grouped:
                grouped[key] = {"timestamp": ts, "ticket_id": tid, "user": user, "steps": []}
                order.append(key)
            grouped[key]["steps"].append({"field": field, "value": value})
        log_groups = []
        for key in reversed(order):          # newest first
            g = grouped[key]
            g["summary"] = _summarize(g["steps"])
            log_groups.append(g)

        # Cap the default view — the log grows forever and rendering
        # thousands of rows slows the page. ?all=1 shows everything.
        show_all  = request.args.get('all') == '1'
        truncated = False
        if not show_all and len(log_groups) > 500:
            log_groups = log_groups[:500]
            truncated  = True

        return render_template('history.html',
            log_headers=log_headers, log_rows=log_rows, log_groups=log_groups,
            truncated=truncated,
            ticket_id=ticket_id, filter_user=filter_user,
            is_admin=session.get('user_email') in ADMIN_USERS,
            is_employee=session.get('user_email') in ALLOWED_USERS)
    except Exception:
        log.exception("History error")
        return "History error", 500

@app.route('/download_log_csv')
def download_log_csv():
    auth = _require_auth()
    if auth: return auth
    if log_sheet is None: return "❌ Log sheet not connected.", 500
    try:
        data = log_sheet.get_all_values()
        if not data: return "No log data.", 404
        output = io.StringIO()
        csv.writer(output).writerows(data)
        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=full_log.csv"
        return response
    except Exception as e:
        return f"Log CSV error: {e}", 500

@app.route('/delete_log_entry', methods=['POST'])
def delete_log_entry():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if _require_admin(): return jsonify({"error": "Admins only."}), 403
    try:
        payload   = request.get_json(silent=True) or {}
        entry_id  = (payload.get('entry_id')  or "").strip()
        tid       = (payload.get('ticket_id') or "").strip()
        usr       = (payload.get('user')      or "").strip()
        if not entry_id or not log_sheet:
            return jsonify({"error": "Missing entry ID"}), 400
        all_rows = log_sheet.get_all_values()
        # Delete every row in the action group (same timestamp — and same
        # ticket/user when provided). Bottom-up so indexes stay valid.
        to_delete = []
        for r_idx, row in enumerate(all_rows[1:], start=2):
            if not row: continue
            if (row[0] or "").strip() != entry_id: continue
            if tid and (len(row) < 2 or (row[1] or "").strip() != tid): continue
            if usr and (len(row) < 5 or (row[4] or "").strip() != usr): continue
            to_delete.append(r_idx)
        if not to_delete:
            return jsonify({"error": "Not found"}), 404
        # Collapse contiguous row indexes into ranges — grouped actions are
        # appended together so this is usually a single API call instead of N.
        to_delete.sort()
        ranges = []
        start = prev = to_delete[0]
        for idx in to_delete[1:]:
            if idx == prev + 1:
                prev = idx
            else:
                ranges.append((start, prev))
                start = prev = idx
        ranges.append((start, prev))
        for r_start, r_end in reversed(ranges):
            log_sheet.delete_rows(r_start, r_end)
        return jsonify({"success": True, "deleted": len(to_delete)})
    except Exception:
        log.exception("Delete log error")
        return jsonify({"error": "Delete log failed"}), 500

# ========== RECYCLE BIN VIEW ==========
@app.route('/recycle_bin')
def recycle_bin_view():
    auth = _require_auth()
    if auth: return auth
    if _require_admin(): return "Permission denied.", 403
    try:
        if not recycle_sheet: return "Recycle Bin not available.", 500
        all_rows   = recycle_sheet.get_all_values()
        headers    = normalize_headers(all_rows[0]) if all_rows else []
        rows       = all_rows[1:] if len(all_rows) > 1 else []
        if "ticket_id" not in headers:
            return "ticket_id column missing in Recycle Bin", 500
        ticket_ids = []
        tid_idx    = headers.index("ticket_id")
        for row in rows:
            tid = row[tid_idx].strip() if tid_idx < len(row) else ""
            if not tid:
                for cell in row:
                    if str(cell).startswith("TXT-"):
                        tid = str(cell).strip()
                        break
            ticket_ids.append(tid)
        return render_template('recycle_bin.html',
            headers=headers, rows=rows, is_admin=True,
            ticket_ids=ticket_ids,
            restore_url=url_for('restore_ticket'),
            delete_url=url_for('delete_recycle_entry'))
    except Exception:
        log.exception("Recycle bin error")
        return "Recycle bin error", 500

# ========== ANALYTICS PAGES ==========
@app.route('/analytics')
def analytics():
    auth = _require_auth()
    if auth: return auth
    return render_template('analytics.html')

# Legacy Tasks data cache — the sheet has thousands of rows. Keep the TTL
# short (90s) so the big list doesn't sit resident in a 512MB worker for
# long; just long enough to serve a burst of filter changes cheaply.
_legacy_cache = {"data": None, "ts": 0}
_LEGACY_TTL = 90
def get_legacy_values_cached():
    import time
    now = time.time()
    if _legacy_cache["data"] is not None and now - _legacy_cache["ts"] < _LEGACY_TTL:
        return _legacy_cache["data"]
    doc  = client.open_by_key(LEGACY_SHEET_KEY)
    ws   = doc.worksheet("Tasks")
    data = ws.get_all_values()
    _legacy_cache["data"] = data
    _legacy_cache["ts"]   = now
    return data

def _expire_legacy_cache_if_stale():
    """Proactively drop the big legacy list once it's past TTL so it isn't
    held resident between analytics visits."""
    import time
    if (_legacy_cache["data"] is not None and
            time.time() - _legacy_cache["ts"] >= _LEGACY_TTL):
        _legacy_cache["data"] = None

@app.route('/legacy_analytics')
def legacy_analytics():
    auth = _require_auth()
    if auth: return auth
    try:
        data    = get_legacy_values_cached()
        headers = data[0] if data else []
        rows    = data[1:] if len(data) > 1 else []
        hidx    = {_norm_key(h): i for i, h in enumerate(headers)}

        # Deduplicate by Task ID — unique per ticket, stable across imports.
        # The sheet sometimes has rows imported twice with identical Task IDs.
        task_id_idx = hidx.get(_norm_key("Task ID"))
        if task_id_idx is not None:
            seen_ids = set()
            deduped  = []
            for r in rows:
                if not any(c.strip() for c in r):
                    continue
                tid = r[task_id_idx].strip() if task_id_idx < len(r) else ""
                if tid and tid in seen_ids:
                    continue
                if tid:
                    seen_ids.add(tid)
                deduped.append(r)
            rows = deduped
        else:
            rows = [r for r in rows if any(c.strip() for c in r)]

        def col(r, *names):
            for n in names:
                i = hidx.get(_norm_key(n))
                if i is not None and i < len(r):
                    return (r[i] or "").strip()
            return ""

        # ── Filters ─────────────────────────────────────────────────
        start_filter  = (request.args.get("start")  or "").strip()
        end_filter    = (request.args.get("end")    or "").strip()
        lob_filter    = (request.args.get("lob")    or "").strip()
        type_filter   = (request.args.get("rtype")  or "").strip()
        person_filter = (request.args.get("person") or "").strip()
        start_dt = parse_date(start_filter, as_date=True) if start_filter else None
        end_dt   = parse_date(end_filter,   as_date=True) if end_filter   else None

        all_lobs, all_types, all_people = set(), set(), set()
        recs = []
        for r in rows:
            created   = parse_date(col(r, "Created Date"),   as_date=True)
            completed = parse_date(col(r, "Completed Date"), as_date=True)
            progress  = col(r, "Progress")
            rtype     = col(r, "WFM Request") or "Unspecified"
            lob       = col(r, "LOB") or "Unspecified"
            priority  = col(r, "Priority") or "Unspecified"
            late      = col(r, "Late").lower() in ("true", "1", "yes")
            assigned  = col(r, "Assigned To")
            people    = [p.strip() for p in assigned.split(";") if p.strip()] or ["Unassigned"]

            if lob   != "Unspecified": all_lobs.add(lob)
            if rtype != "Unspecified": all_types.add(rtype)
            for p in people:
                if p != "Unassigned": all_people.add(p)

            if start_dt and (not created or created < start_dt): continue
            if end_dt   and (not created or created > end_dt):   continue
            if lob_filter    and lob   != lob_filter:            continue
            if type_filter   and rtype != type_filter:           continue
            if person_filter and person_filter not in people:    continue

            recs.append({"created": created, "completed": completed,
                         "progress": progress, "rtype": rtype, "lob": lob,
                         "people": people, "late": late, "priority": priority})

        # ── KPIs ────────────────────────────────────────────────────
        total       = len(recs)
        completed_n = sum(1 for x in recs if x["progress"].lower() == "completed")
        inprog      = sum(1 for x in recs if "progress" in x["progress"].lower())
        notstart    = sum(1 for x in recs
                          if x["progress"].lower() not in ("completed",)
                          and "progress" not in x["progress"].lower())
        sameday     = sum(1 for x in recs if x["created"] and x["completed"]
                          and x["created"] == x["completed"])
        late_n      = sum(1 for x in recs if x["late"])
        comp_days   = [(x["completed"] - x["created"]).days for x in recs
                       if x["created"] and x["completed"] and x["completed"] >= x["created"]]
        avg_days    = round(sum(comp_days) / len(comp_days), 1) if comp_days else 0
        sameday_pct = round(sameday / completed_n * 100, 1) if completed_n else 0

        # ── Daily volume trend ──────────────────────────────────────
        vol = {}
        for x in recs:
            if x["created"]:
                k = x["created"].strftime("%Y-%m-%d")
                vol[k] = vol.get(k, 0) + 1
        volume_chart = sorted(vol.items())
        # If more than 60 days, aggregate to weekly buckets for readability
        if len(volume_chart) > 60:
            from datetime import datetime as _dt, timedelta as _td
            weekly = {}
            for day_str, cnt in volume_chart:
                d = _dt.strptime(day_str, "%Y-%m-%d")
                # Week starting Monday
                week_start = (d - _td(days=d.weekday())).strftime("%Y-%m-%d")
                weekly[week_start] = weekly.get(week_start, 0) + cnt
            volume_chart = sorted(weekly.items())
            volume_chart_label = "weekly"
        else:
            volume_chart_label = "daily"

        # ── Breakdowns ──────────────────────────────────────────────
        type_counts = Counter(x["rtype"]    for x in recs).most_common()
        lob_counts  = Counter(x["lob"]      for x in recs).most_common()
        prio_counts = Counter(x["priority"] for x in recs).most_common()

        # ── Team table ──────────────────────────────────────────────
        team = {}
        for x in recs:
            for p in x["people"]:
                t = team.setdefault(p, {"assigned": 0, "completed": 0,
                                        "inprog": 0, "notstart": 0, "sameday": 0})
                t["assigned"] += 1
                pr = x["progress"].lower()
                if pr == "completed":
                    t["completed"] += 1
                    if x["created"] and x["completed"] and x["created"] == x["completed"]:
                        t["sameday"] += 1
                elif "progress" in pr:
                    t["inprog"] += 1
                else:
                    t["notstart"] += 1
        team_rows = []
        for p, t in sorted(team.items(), key=lambda kv: -kv[1]["assigned"]):
            pct = round(t["sameday"] / t["completed"] * 100, 1) if t["completed"] else 0
            team_rows.append({"name": p, **t, "sameday_pct": pct})

        # ── Completion by day (most recent 14 in range) ─────────────
        comp_by_day = {}
        for x in recs:
            if not x["created"]: continue
            k = x["created"].strftime("%Y-%m-%d")
            c = comp_by_day.setdefault(k, {"created": 0, "sameday": 0,
                                           "inprog": 0, "pending": 0})
            c["created"] += 1
            pr = x["progress"].lower()
            if pr == "completed" and x["completed"] == x["created"]:
                c["sameday"] += 1
            elif "progress" in pr:
                c["inprog"] += 1
            elif pr != "completed":
                c["pending"] += 1
        completion_days = sorted(comp_by_day.items(), reverse=True)[:14]

        # free the big per-record list before rendering; drop the cached
        # sheet data if it's aged out so it doesn't sit resident
        recs.clear()
        _expire_legacy_cache_if_stale()
        gc.collect()

        return render_template("legacy_analytics.html",
            total=total, completed_n=completed_n, inprog=inprog,
            notstart=notstart, sameday=sameday, sameday_pct=sameday_pct,
            late_n=late_n, avg_days=avg_days,
            volume_chart=volume_chart, volume_chart_label=volume_chart_label,
            type_counts=type_counts,
            lob_counts=lob_counts, prio_counts=prio_counts,
            team_rows=team_rows, completion_days=completion_days,
            all_lobs=sorted(all_lobs), all_types=sorted(all_types),
            all_people=sorted(all_people),
            start=start_filter, end=end_filter, lob=lob_filter,
            rtype=type_filter, person=person_filter)
    except Exception:
        log.exception("Legacy analytics error")
        return "Legacy analytics error", 500

# ========== SCHEDULE TOOLS ==========
@app.route('/schedule-tools')
def schedule_tools():
    auth = _require_auth()
    if auth: return auth
    return render_template('schedule_tools.html')

@app.route('/run-schedule', methods=['GET', 'POST'])
def run_schedule():
    auth = _require_auth()
    if auth: return auth
    if request.method == 'GET':
        html = render_template('schedule_input.html')
        # Inject localStorage prefill/save JS
        schedule_js = '''
<script>
// ── Schedule Tools — localStorage prefill & save ──────────────────
var SCHED_FIELDS = ['ss_team_leads','ps_team_leads','ss_closing_rotation',
  'ss_sunday_rotation','ps_friday_rotation','ps_weekend_tl',
  'ps_monday_thursday_tl','ss_saturday_primary_tl','schedule_start','schedule_end',
  'shift_ss_weekday_close','shift_ss_weekday_open','shift_ps_weekday_close',
  'shift_ps_weekday_open','shift_ss_saturday_open','shift_ss_saturday_close',
  'shift_ss_sunday_close'];

// Load saved values on page open
document.addEventListener('DOMContentLoaded', function() {
  SCHED_FIELDS.forEach(function(name) {
    var saved = localStorage.getItem('sched_' + name);
    if (!saved) return;
    var el = document.querySelector('[name="' + name + '"]');
    if (el) el.value = saved;
  });
  // Add save reminder
  var btn = document.querySelector('button[type="submit"], input[type="submit"]');
  if (btn && btn.form) {
    btn.form.addEventListener('submit', function() {
      SCHED_FIELDS.forEach(function(name) {
        var el = document.querySelector('[name="' + name + '"]');
        if (el && el.value) localStorage.setItem('sched_' + name, el.value);
      });
    });
  }
  // Add clear button after submit button
  var submitBtn = document.querySelector('button[type="submit"]');
  if (submitBtn) {
    var clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.textContent = '🗑 Clear Saved';
    clearBtn.style.cssText = 'margin-left:8px;padding:8px 14px;background:#e9ecef;border:1px solid #ccc;border-radius:6px;cursor:pointer;font-size:.85rem';
    clearBtn.onclick = function() {
      if (confirm('Clear all saved schedule data?')) {
        SCHED_FIELDS.forEach(function(n){ localStorage.removeItem('sched_' + n); });
        location.reload();
      }
    };
    submitBtn.parentNode.insertBefore(clearBtn, submitBtn.nextSibling);
  }
});
</script>'''
        if '</body>' in html:
            html = html.replace('</body>', schedule_js + '</body>', 1)
        return html
    try:
        ss_list               = [x.strip() for x in request.form.get("ss_team_leads", "").split("\n") if x.strip()]
        ps_list               = [x.strip() for x in request.form.get("ps_team_leads", "").split("\n") if x.strip()]
        ss_closing_rotation   = [x.strip() for x in request.form.get("ss_closing_rotation", "").split("\n") if x.strip()]
        ss_sunday_rotation    = [x.strip() for x in request.form.get("ss_sunday_rotation",  "").split("\n") if x.strip()]
        ps_friday_rotation    = [x.strip() for x in request.form.get("ps_friday_rotation",  "").split("\n") if x.strip()]
        ps_weekend_tl         = request.form.get("ps_weekend_tl",         "").strip()
        ps_monday_thursday_tl = request.form.get("ps_monday_thursday_tl", "").strip()
        ss_saturday_primary   = request.form.get("ss_saturday_primary_tl","").strip()

        def _int(key, default=0):
            try:    return int(request.form.get(key, default))
            except: return default

        ss_skip_start  = _int("ss_skip_start", 0)
        ps_fri_start   = _int("ps_fri_start",  0)
        ss_sun_start   = _int("ss_sun_start",  0)
        schedule_start = request.form.get("schedule_start", "").strip()
        schedule_end   = request.form.get("schedule_end",   "").strip()

        accommodations = {}
        for tl in ss_list + ps_list:
            safe      = tl.replace(" ", "_")
            multiweek = bool(request.form.get(f"accom_{safe}_multiweek"))
            row = {"notes": request.form.get(f"accom_notes_{safe}", "").strip(),
                   "multiweek": multiweek, "week1": {}, "week2": {}}
            for week in ["week1", "week2"]:
                for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
                    avail = request.form.get(f"accom_{safe}_{week}_{day}", "fill")
                    start = request.form.get(f"accom_{safe}_{week}_{day}_start", "").strip()
                    end   = request.form.get(f"accom_{safe}_{week}_{day}_end",   "").strip()
                    row[week][day] = {"avail": avail, "start": start or None, "end": end or None}
            if not multiweek:
                row["week2"] = row["week1"]
            for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
                row[day] = row["week1"].get(day, {}).get("avail", "fill")
            accommodations[tl] = row

        pto_names  = request.form.getlist("pto_name[]")
        pto_starts = request.form.getlist("pto_start[]")
        pto_ends   = request.form.getlist("pto_end[]")
        pto_types  = request.form.getlist("pto_type[]")
        pto_notes  = request.form.getlist("pto_note[]")
        pto = []
        for i, (name, start, end) in enumerate(zip(pto_names, pto_starts, pto_ends)):
            if name.strip() and start.strip() and end.strip():
                pto.append({
                    "name":  name.strip(),
                    "start": start.strip(),
                    "end":   end.strip(),
                    "type":  pto_types[i].strip() if i < len(pto_types) else "full",
                    "note":  pto_notes[i].strip()  if i < len(pto_notes)  else "",
                })

        holidays = []
        for date, name in zip(
            request.form.getlist("holiday_date[]"),
            request.form.getlist("holiday_name[]")):
            if date.strip():
                holidays.append({"date": date.strip(), "name": name.strip()})

        shift_times = {
            "SS_weekday_close":  request.form.get("shift_ss_weekday_close",  "13:30\u201322:00"),
            "SS_weekday_open":   request.form.get("shift_ss_weekday_open",   "08:00\u201316:30"),
            "PS_weekday_close":  request.form.get("shift_ps_weekday_close",  "11:30\u201320:00"),
            "PS_weekday_open":   request.form.get("shift_ps_weekday_open",   "08:00\u201316:30"),
            "SS_saturday_open":  request.form.get("shift_ss_saturday_open",  "09:00\u201317:30"),
            "SS_saturday_close": request.form.get("shift_ss_saturday_close", "10:30\u201319:00"),
            "SS_sunday_close":   request.form.get("shift_ss_sunday_close",   "09:30\u201318:00"),
        }

        results = schedule_builder.build_schedule(
            ss_list=ss_list, ps_list=ps_list,
            ss_closing_rotation=ss_closing_rotation,
            ss_sunday_rotation=ss_sunday_rotation,
            ps_friday_rotation=ps_friday_rotation,
            ps_weekend_tl=ps_weekend_tl,
            ps_monday_thursday_tl=ps_monday_thursday_tl,
            ss_saturday_primary_tl=ss_saturday_primary,
            ss_skip_start=ss_skip_start, ps_fri_start=ps_fri_start, ss_sun_start=ss_sun_start,
            schedule_start=schedule_start, schedule_end=schedule_end,
            accommodations=accommodations, pto=pto, holidays=holidays, shift_times=shift_times)

        import uuid
        sid = session.get('_id') or str(uuid.uuid4())
        session['_id'] = sid
        _schedule_store[sid] = {
            "text_report": results.get("text_report", ""),
            "html_report": results.get("html_report", ""),
            "excel_bytes": results.get("excel_bytes"),
        }
        if request.form.get("output_format") == "xlsx":
            return redirect(url_for('download_schedule_xlsx'))
        return redirect(url_for('schedule_report'))
    except Exception as e:
        log.exception("Schedule error")
        return f"Schedule error: {str(e)}", 500

@app.route('/schedule-report')
def schedule_report():
    auth = _require_auth()
    if auth: return auth
    sid     = session.get('_id', '')
    reports = _schedule_store.get(sid, {})
    return render_template("templates_schedule_report.html",
        text_report=reports.get("text_report", ""),
        html_report=reports.get("html_report", ""))

@app.route('/download-schedule-xlsx')
def download_schedule_xlsx():
    auth = _require_auth()
    if auth: return auth
    sid     = session.get('_id', '')
    reports = _schedule_store.get(sid, {})
    buf     = reports.get("excel_bytes")
    if not buf: return "No schedule generated yet.", 404
    try:    buf.seek(0)
    except: return "Excel data unavailable.", 500
    return Response(buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=TL_Rotation_Schedule.xlsx"})

# ========== LEGACY TICKETS ==========
@app.route('/legacy_tickets')
def legacy_tickets():
    auth = _require_auth()
    if auth: return auth
    try:
        headers, rows = get_legacy_rows()

        # Deduplicate — sheet sometimes has exact duplicate rows from multiple imports
        seen = set()
        deduped = []
        for r in rows:
            key = tuple(r)
            if key not in seen and any(c.strip() for c in r):
                seen.add(key)
                deduped.append(r)
        rows = deduped
        start_dt = parse_date_safe(request.args.get("start")) if request.args.get("start") else None
        end_dt   = parse_date_safe(request.args.get("end"))   if request.args.get("end")   else None
        if start_dt or end_dt:
            date_col_names = ["Created Date", "Start Date", "Due Date", "Completed Date"]
            date_idxs = [i for i, h in enumerate(headers) if h.strip() in date_col_names]
            if not date_idxs:
                date_idxs = [i for i in [7, 8, 9, 12] if i < len(headers)]
            filtered = []
            for r in rows:
                for idx in date_idxs:
                    if idx >= len(r): continue
                    cell = (r[idx] or "").strip()
                    if not cell: continue
                    rd = parse_date_safe(cell)
                    if rd and (not start_dt or rd >= start_dt) and (not end_dt or rd <= end_dt):
                        filtered.append(r); break
            rows = filtered
        return render_template("legacy_dashboard.html",
            headers=headers, rows=rows,
            is_admin=session.get('user_email') in ADMIN_USERS,
            is_employee=session.get('user_email') in ALLOWED_USERS)
    except Exception:
        log.exception("Legacy ticket error")
        return "Legacy ticket load error", 500

# ========== API LOGS ==========
@app.route('/api/logs')
def api_logs():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if log_sheet is None: return jsonify({"error": "Log sheet not connected"}), 500
    try:
        data = log_sheet.get_all_values()
        if not data: return jsonify([])
        headers = data[0]
        return jsonify([dict(zip(headers, row)) for row in data[1:]])
    except Exception:
        return jsonify({"error": "api_logs error"}), 500

# ========== ANALYTICS API ==========
@app.route('/api/ticket_volume')
def api_ticket_volume():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({})
    try:
        sub_idx = next(i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at")
    except StopIteration:
        return jsonify({})
    from_date, to_date = parse_filter_dates()
    counts = Counter()
    for row in rows:
        d = parse_date_safe(row[sub_idx] if sub_idx < len(row) else "")
        if not d or (from_date and d < from_date) or (to_date and d > to_date): continue
        counts[d.isoformat()] += 1
    return jsonify({k: counts[k] for k in sorted(counts)})

@app.route('/api/status_breakdown')
def api_status_breakdown():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({"open": 0, "pending": 0, "closed": 0})
    try:
        status_idx = next(i for i, h in enumerate(headers) if (h or "").strip().lower() == "status")
    except StopIteration:
        return jsonify({"open": 0, "pending": 0, "closed": 0})
    sub_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    from_date, to_date = parse_filter_dates()
    counts = {"open": 0, "pending": 0, "closed": 0}
    for row in rows:
        if sub_idx is not None:
            d = parse_date_safe(row[sub_idx] if sub_idx < len(row) else "")
            if d and ((from_date and d < from_date) or (to_date and d > to_date)): continue
        s = (row[status_idx] if status_idx < len(row) else "").strip().lower()
        if "closed" in s:   counts["closed"]  += 1
        elif "pend" in s:   counts["pending"] += 1
        elif s:             counts["open"]    += 1
    return jsonify(counts)

@app.route('/api/workload_distribution')
def api_workload_distribution():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({})
    try:
        assigned_idx = next(i for i, h in enumerate(headers) if (h or "").strip().lower() == "assigned to")
    except StopIteration:
        return jsonify({})
    status_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "status"), None)
    sub_idx    = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    from_date, to_date = parse_filter_dates()
    data = defaultdict(lambda: {"open": 0, "pending": 0, "closed": 0})
    for row in rows:
        if sub_idx is not None:
            d = parse_date_safe(row[sub_idx] if sub_idx < len(row) else "")
            if d and ((from_date and d < from_date) or (to_date and d > to_date)): continue
        agent  = (row[assigned_idx] if assigned_idx < len(row) else "").strip() or "Unassigned"
        status = (row[status_idx]   if status_idx is not None and status_idx < len(row) else "").strip().lower()
        if "closed" in status:   data[agent]["closed"]  += 1
        elif "pend" in status:   data[agent]["pending"] += 1
        else:                    data[agent]["open"]    += 1
    out = {}
    for agent, v in data.items():
        total = v["open"] + v["pending"] + v["closed"]
        out[agent] = {**v, "completion_rate": round(v["closed"] / total * 100, 1) if total else 0}
    return jsonify(out)

@app.route('/api/wfm_request_breakdown')
def api_wfm_request_breakdown():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({})
    try:
        req_idx = next(i for i, h in enumerate(headers) if (h or "").strip().lower() == "wfm_request")
    except StopIteration:
        return jsonify({})
    status_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "status"), None)
    sub_idx    = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    from_date, to_date = parse_filter_dates()
    data = defaultdict(lambda: {"open": 0, "closed": 0})
    for row in rows:
        if sub_idx is not None:
            d = parse_date_safe(row[sub_idx] if sub_idx < len(row) else "")
            if d and ((from_date and d < from_date) or (to_date and d > to_date)): continue
        key    = (row[req_idx]    if req_idx < len(row) else "").strip() or "Unknown"
        status = (row[status_idx] if status_idx is not None and status_idx < len(row) else "").strip().lower()
        if "closed" in status: data[key]["closed"] += 1
        else:                  data[key]["open"]   += 1
    out = {}
    for k, v in data.items():
        total = v["open"] + v["closed"]
        out[k] = {**v, "completion_rate": round(v["closed"] / total * 100, 1) if total else 0}
    return jsonify(out)

@app.route('/api/avg_resolution_time')
def api_avg_resolution_time():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({"average_resolution_hours": 0.0})
    def find_idx(name):
        return next((i for i, h in enumerate(headers) if (h or "").strip().lower() == name), None)
    sub_idx    = find_idx("submitted at")
    closed_idx = find_idx("closed at")
    status_idx = find_idx("status")
    from_date, to_date = parse_filter_dates()
    durations = []
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
    for row in rows:
        if status_idx is None: continue
        if "closed" not in (row[status_idx] if status_idx < len(row) else "").strip().lower(): continue
        subval   = row[sub_idx]    if sub_idx    is not None and sub_idx    < len(row) else ""
        closeval = row[closed_idx] if closed_idx is not None and closed_idx < len(row) else ""
        d = parse_date_safe(subval)
        if d and ((from_date and d < from_date) or (to_date and d > to_date)): continue
        try:
            dt_sub = dt_close = None
            for fmt in fmts:
                try:    dt_sub   = datetime.strptime(subval,   fmt); break
                except: pass
            for fmt in fmts:
                try:    dt_close = datetime.strptime(closeval, fmt); break
                except: pass
            if dt_sub and dt_close:
                h = (dt_close - dt_sub).total_seconds() / 3600
                if h >= 0: durations.append(h)
        except Exception:
            continue
    return jsonify({"average_resolution_hours": round(sum(durations) / len(durations), 1) if durations else 0.0})

# ========== LEGACY ANALYTICS API ==========
def _legacy_date_filter(data, from_date, to_date):
    if not from_date and not to_date: return data
    filtered = []
    for row in data:
        d = parse_date_safe(row.get("Created Date", ""))
        if d and (not from_date or d >= from_date) and (not to_date or d <= to_date):
            filtered.append(row)
    return filtered

@app.route('/api/legacy_ticket_volume')
def legacy_ticket_volume():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    from_date, to_date = parse_filter_dates()
    data   = _legacy_date_filter(get_legacy_data(), from_date, to_date)
    volume = {}
    for row in data:
        date = row.get("Created Date")
        if date:
            try:    key = datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d")
            except: key = date
            volume[key] = volume.get(key, 0) + 1
    return jsonify(volume)

@app.route('/api/legacy_status_breakdown')
def legacy_status_breakdown():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    from_date, to_date = parse_filter_dates()
    data   = _legacy_date_filter(get_legacy_data(), from_date, to_date)
    counts = {"open": 0, "pending": 0, "closed": 0}
    for row in data:
        p = row.get("Progress", "").lower()
        if "not started" in p:   counts["open"]    += 1
        elif "in progress" in p: counts["pending"] += 1
        elif "completed"   in p: counts["closed"]  += 1
    return jsonify(counts)

@app.route('/api/legacy_workload_distribution')
def legacy_workload_distribution():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    from_date, to_date = parse_filter_dates()
    data     = _legacy_date_filter(get_legacy_data(), from_date, to_date)
    workload = {}
    for row in data:
        assignee = row.get("Assigned To", "Unassigned")
        p        = row.get("Progress", "").lower()
        if assignee not in workload:
            workload[assignee] = {"open": 0, "pending": 0, "closed": 0, "completion_rate": 0}
        if "not started" in p:   workload[assignee]["open"]    += 1
        elif "in progress" in p: workload[assignee]["pending"] += 1
        elif "completed"   in p: workload[assignee]["closed"]  += 1
    for a, s in workload.items():
        total = s["open"] + s["pending"] + s["closed"]
        s["completion_rate"] = round(s["closed"] / total * 100, 1) if total else 0
    return jsonify(workload)

@app.route('/api/legacy_wfm_request_breakdown')
def legacy_wfm_request_breakdown():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    from_date, to_date = parse_filter_dates()
    data      = _legacy_date_filter(get_legacy_data(), from_date, to_date)
    breakdown = {}
    for row in data:
        req = row.get("WFM Request", "Unknown")
        p   = row.get("Progress", "").lower()
        if req not in breakdown:
            breakdown[req] = {"open": 0, "closed": 0, "completion_rate": 0}
        if "completed" in p: breakdown[req]["closed"] += 1
        else:                breakdown[req]["open"]   += 1
    for req, s in breakdown.items():
        total = s["open"] + s["closed"]
        s["completion_rate"] = round(s["closed"] / total * 100, 1) if total else 0
    return jsonify(breakdown)

@app.route('/api/legacy_avg_resolution_time')
def legacy_avg_resolution_time():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    from_date, to_date = parse_filter_dates()
    data        = _legacy_date_filter(get_legacy_data(), from_date, to_date)
    total_hours = 0
    count       = 0
    for row in data:
        created   = row.get("Created Date")
        completed = row.get("Completed Date")
        if created and completed:
            try:
                diff         = datetime.strptime(completed, "%m/%d/%Y") - datetime.strptime(created, "%m/%d/%Y")
                total_hours += diff.total_seconds() / 3600
                count       += 1
            except Exception:
                continue
    return jsonify({"average_resolution_hours": round(total_hours / count, 2) if count else 0})

@app.route('/export_legacy_csv')
def export_legacy_csv():
    auth = _require_auth()
    if auth: return auth
    doc    = client.open_by_key(LEGACY_SHEET_KEY)
    ws     = doc.worksheet("Tasks")
    output = io.StringIO()
    csv.writer(output).writerows(ws.get_all_values())
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=legacy_tickets.csv"})

# ========== SERVICE LEVEL ==========
def _sl_parse_params():
    from_date, to_date = parse_filter_dates()
    try:    threshold_hrs = float(request.args.get('threshold', 8))
    except: threshold_hrs = 8.0
    return from_date, to_date, threshold_hrs

def _sl_resolution_hours(submitted_str, closed_str):
    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
    dt_sub = dt_close = None
    for fmt in fmts:
        try:
            if not dt_sub:   dt_sub   = datetime.strptime(submitted_str, fmt)
        except: pass
        try:
            if not dt_close: dt_close = datetime.strptime(closed_str, fmt)
        except: pass
    if dt_sub and dt_close:
        h = (dt_close - dt_sub).total_seconds() / 3600
        return h if h >= 0 else None
    return None

@app.route('/service_level')
def service_level():
    auth = _require_auth()
    if auth: return auth
    return render_template('service_level.html')

@app.route('/api/service_level_kpis')
def api_service_level_kpis():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({"total_tickets": 0, "answered_within": 0, "service_level": 0})
    from_date, to_date, threshold_hrs = _sl_parse_params()
    sub_idx    = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    closed_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "closed at"),    None)
    total = within = 0
    for row in rows:
        sub_val = row[sub_idx] if sub_idx is not None and sub_idx < len(row) else ""
        d = parse_date_safe(sub_val)
        if d and from_date and d < from_date: continue
        if d and to_date   and d > to_date:   continue
        total += 1
        if closed_idx is not None and sub_idx is not None:
            hrs = _sl_resolution_hours(sub_val, row[closed_idx] if closed_idx < len(row) else "")
            if hrs is not None and hrs <= threshold_hrs:
                within += 1
    sl = round(within / total * 100, 1) if total else 0
    return jsonify({"total_tickets": total, "answered_within": within, "service_level": sl})

@app.route('/api/service_level_trend')
def api_service_level_trend():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({})
    from_date, to_date, threshold_hrs = _sl_parse_params()
    sub_idx    = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    closed_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "closed at"),    None)
    if sub_idx is None: return jsonify({})
    daily_total  = defaultdict(int)
    daily_within = defaultdict(int)
    for row in rows:
        sub_val = row[sub_idx] if sub_idx < len(row) else ""
        d = parse_date_safe(sub_val)
        if not d: continue
        if from_date and d < from_date: continue
        if to_date   and d > to_date:   continue
        key = d.isoformat()
        daily_total[key] += 1
        if closed_idx is not None:
            hrs = _sl_resolution_hours(sub_val, row[closed_idx] if closed_idx < len(row) else "")
            if hrs is not None and hrs <= threshold_hrs:
                daily_within[key] += 1
    result = {}
    for date in sorted(daily_total):
        t = daily_total[date]
        result[date] = round(daily_within.get(date, 0) / t * 100, 1) if t else 0
    return jsonify(result)

@app.route('/api/service_level_breakdown')
def api_service_level_breakdown():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({})
    from_date, to_date, threshold_hrs = _sl_parse_params()
    sub_idx    = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    closed_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "closed at"),    None)
    if sub_idx is None: return jsonify({})
    daily = defaultdict(lambda: {"within": 0, "outside": 0})
    for row in rows:
        sub_val = row[sub_idx] if sub_idx < len(row) else ""
        d = parse_date_safe(sub_val)
        if not d: continue
        if from_date and d < from_date: continue
        if to_date   and d > to_date:   continue
        key = d.isoformat()
        if closed_idx is not None:
            hrs = _sl_resolution_hours(sub_val, row[closed_idx] if closed_idx < len(row) else "")
            if hrs is not None and hrs <= threshold_hrs: daily[key]["within"]  += 1
            else:                                        daily[key]["outside"] += 1
        else:
            daily[key]["outside"] += 1
    return jsonify({k: daily[k] for k in sorted(daily)})

@app.route('/api/service_level_forecast')
def api_service_level_forecast():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    headers, rows = get_sheet_values_safe(include_archive=True)
    if not headers: return jsonify({"labels": [], "actual": [], "forecast": []})
    from_date, to_date, _ = _sl_parse_params()
    sub_idx = next((i for i, h in enumerate(headers) if (h or "").strip().lower() == "submitted at"), None)
    if sub_idx is None: return jsonify({"labels": [], "actual": [], "forecast": []})
    daily = defaultdict(int)
    for row in rows:
        sub_val = row[sub_idx] if sub_idx < len(row) else ""
        d = parse_date_safe(sub_val)
        if not d: continue
        if from_date and d < from_date: continue
        if to_date   and d > to_date:   continue
        daily[d.isoformat()] += 1
    actual_dates  = sorted(daily.keys())
    actual_values = [daily[k] for k in actual_dates]
    forecast_extension = []
    if len(actual_values) >= 3:
        window    = actual_values[-7:] if len(actual_values) >= 7 else actual_values
        avg_daily = sum(window) / len(window)
        slope     = (window[-1] - window[0]) / max(len(window) - 1, 1)
        last_date = datetime.strptime(actual_dates[-1], "%Y-%m-%d")
        for i in range(1, 8):
            fd = (last_date + timedelta(days=i)).strftime("%Y-%m-%d")
            forecast_extension.append((fd, max(0, round(avg_daily + slope * i))))
    n = len(actual_dates)
    m = len(forecast_extension)
    return jsonify({
        "labels":   actual_dates + [fd for fd, _ in forecast_extension],
        "actual":   actual_values + [None] * m,
        "forecast": [None] * n + [fv for _, fv in forecast_extension]
    })

@app.route('/wfm-checklist')
def wfm_checklist():
    auth = _require_auth()
    if auth: return auth
    return render_template('wfm_checklist.html')

@app.route('/wfm-checklist/export')
def wfm_checklist_export():
    auth = _require_auth()
    if auth: return auth
    from_date = (request.args.get('from') or '').strip()
    to_date   = (request.args.get('to')   or '').strip()
    if checklist_sheet is None:
        return "ChecklistState sheet not available", 500
    try:
        rows = checklist_sheet.get_all_values()
        if not rows:
            return "No data in sheet", 404

        # Build a task label lookup from the task column (col D)
        import io as _io, csv as _csv

        output = _io.StringIO()
        w = _csv.writer(output)
        w.writerow(['Date', 'Kind', 'Item / ID', 'Task / Detail', 'Value', 'Who', 'Time'])

        for r in rows[1:]:
            if len(r) < 7:
                continue
            date, kind, item_id, task, value, who, updated_at = (
                (r[0] or '').strip(), (r[1] or '').strip(), (r[2] or '').strip(),
                (r[3] or '').strip(), (r[4] or '').strip(),
                (r[5] or '').strip(), (r[6] or '').strip(),
            )
            # Date range filter
            if from_date and date < from_date:
                continue
            if to_date and date > to_date:
                continue
            # Make value readable
            if kind == 'tick':
                val_label = 'Done' if value == '1' else 'Unchecked'
            elif kind in ('manager', 'ahod'):
                val_label = value  # name or level
            else:
                # activity: value is JSON — flatten to readable string
                try:
                    import json as _j
                    p = _j.loads(value) if value else {}
                    parts = []
                    for k in ('type','advisor','who_involved','lob','assisting_lob','detail','from','to'):
                        if p.get(k): parts.append(f"{k}: {p[k]}")
                    val_label = ' | '.join(parts) if parts else value
                except Exception:
                    val_label = value
            w.writerow([date, kind, item_id, task, val_label,
                        who.split('@')[0] if '@' in who else who, updated_at])

        output.seek(0)
        from flask import Response
        today_str = datetime.now(ZoneInfo(TIMEZONE)).strftime('%Y%m%d')
        fname = f"wfm_checklist_history_{from_date or 'all'}_{to_date or today_str}.csv"
        return Response(
            '\ufeff' + output.getvalue(),
            mimetype='text/csv;charset=utf-8',
            headers={'Content-Disposition': f'attachment;filename={fname}'}
        )
    except Exception:
        log.exception("checklist export error")
        return "Export failed", 500

# Employee directory for autofill — trimmed to just the fields the
# attendance builder needs, cached 10 min (roster changes rarely).
_empdir_cache = {"list": None, "ts": 0}
_EMPDIR_TTL = 600

def _load_employee_directory():
    import time
    now = time.time()
    if _empdir_cache["list"] is not None and now - _empdir_cache["ts"] < _EMPDIR_TTL:
        return _empdir_cache["list"]
    people = []
    try:
        rdoc, _ = _get_roster_doc()
        if rdoc:
            ws   = rdoc.worksheet("EMPLOYEES")
            data = ws.get_all_values()
            if data and len(data) >= 2:
                hdr = {(_norm_key(h)): i for i, h in enumerate(data[0])}
                def g(row, name):
                    i = hdr.get(_norm_key(name))
                    return (row[i].strip() if i is not None and i < len(row) else "")
                for row in data[1:]:
                    if not any(row): continue
                    first = g(row, "First Name") or g(row, "Address First Name")
                    last  = g(row, "Last Name")  or g(row, "Address Last Name")
                    name  = (first + " " + last).strip()
                    if not name: continue
                    status  = g(row, "Status").lower()
                    deleted = g(row, "Deleted").lower()
                    if deleted in ("1", "true", "yes") or status in ("inactive", "terminated"):
                        continue   # active employees only
                    people.append({
                        "name":  name,
                        "lob":   g(row, "Planning Unit"),
                        "phone": g(row, "Address Phone"),
                        "phone_alt": g(row, "Address Phone Alt"),
                        "email": g(row, "Address Email"),
                        "id":    g(row, "Current ID") or g(row, "Employee ID"),
                    })
            del data
            gc.collect()
    except Exception:
        log.exception("Employee directory load failed")
    people.sort(key=lambda p: p["name"].lower())
    _empdir_cache.update({"list": people, "ts": now})
    return people

@app.route('/api/employee_directory')
def api_employee_directory():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    import rtm as _rtm_mod
    lobs = sorted(set(info[0] for info in _rtm_mod.QUEUE_MAP.values()))
    return jsonify({"employees": _load_employee_directory(), "lobs": lobs})

# ── Shared "who's managing the RTM dashboard" indicator ──────────
# Stored in ChecklistState as a single kind='manager' row (item_id='current').
_manager_cache = {"data": None, "ts": 0}
_MANAGER_TTL = 30  # increased from 15s — reduces checklist_sheet reads at midnight
RTM_MANAGERS = ["Mike", "Dawn", "Josh", "Claudis", "Sara"]

@app.route('/api/rtm_manager')
def api_rtm_manager():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if checklist_sheet is None:
        return jsonify({"manager": "", "since": "", "names": RTM_MANAGERS})
    import time
    now = time.time()
    if _manager_cache["data"] is not None and now - _manager_cache["ts"] < _MANAGER_TTL:
        return jsonify(_manager_cache["data"])
    try:
        rows = checklist_sheet.get_all_values()
        manager, since = "", ""
        today = _est_today_str()
        for r in rows[1:]:
            if len(r) >= 7 and (r[1] or "").strip() == "manager" and (r[2] or "").strip() == "current":
                # Only count today's rows — auto-resets at midnight EST
                if (r[0] or "").strip() != today:
                    continue
                manager = (r[4] or "").strip()
                since   = (r[6] or "").strip()
        data = {"manager": manager, "since": since, "names": RTM_MANAGERS}
        _manager_cache.update({"data": data, "ts": now})
        del rows; gc.collect()
        return jsonify(data)
    except Exception as e:
        log.exception("rtm_manager read error")
        # On 429 or any error, return stale cached data if available
        if _manager_cache["data"] is not None:
            return jsonify(_manager_cache["data"])
        return jsonify({"manager": "", "since": "", "names": RTM_MANAGERS})

@app.route('/api/rtm_manager', methods=['POST'])
def api_rtm_manager_set():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        p          = request.get_json(silent=True) or {}
        name       = (p.get("manager") or "").strip()
        stamp      = datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M")
        changed_by = session.get('user_email', session.get('user', 'unknown'))
        # Use checklist_sheet — re-fetch if worker somehow lost it
        ws = checklist_sheet
        if ws is None:
            ws = main_doc.worksheet("ChecklistState")
        safe_append_row(ws, [_est_today_str(), "manager", "current", name, name, changed_by, stamp])
        _manager_cache["ts"] = 0
        log.info(f"RTM manager set to '{name}' by {changed_by}")
        return jsonify({"success": True, "manager": name, "since": stamp})
    except Exception:
        log.exception("rtm_manager set error")
        return jsonify({"error": "Save failed"}), 500

@app.route('/api/checklist_advisor_info')
def api_checklist_advisor_info():
    """Return an advisor's last agent status time and shift end time for VTO logging."""
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    name = (request.args.get('name') or '').strip()
    if not name or not _rtm:
        return jsonify({"last_status_time": "", "shift_end": ""})
    try:
        today = _rtm._today_est()
        # Last agent status event — scan raw agent status for this person
        raw = _rtm._read_sheet(client, _rtm.AGENT_STATUS_ID)
        pm, nm, _tm = _rtm._load_roster(client)
        # Find current_id for the name
        norm = _rtm._norm_name(name)
        roster = nm.get(norm, {})
        cid = roster.get('current_id', '')
        last_time = ''
        if cid:
            today_str = today.strftime('%Y-%m-%d')
            events = []
            for r in raw:
                uid = str(r.get('user_id', '') or '').strip()
                if uid != cid: continue
                st = str(r.get('start_time', '') or '').strip()
                if not st.startswith(today_str): continue
                dt_cst = _rtm._parse_datetime_str(st)
                if dt_cst:
                    events.append(_rtm._cp_to_est(dt_cst))
            if events:
                last_time = max(events).strftime('%H:%M')
        # Shift end — find in PW schedule blocks for today
        blocks = _rtm._read_sheet(client, _rtm.SCHEDULE_BLOCKS_ID)
        shift_end = ''
        norm_name_lower = name.strip().lower()
        for r in blocks:
            row_date = _rtm._parse_date_flexible(r.get('Date', ''))
            if row_date != today: continue
            emp = str(r.get('Employee Name', '') or '').strip().lower()
            if emp == norm_name_lower or norm_name_lower in emp:
                et = str(r.get('End Time', '') or '').strip()
                if et and (not shift_end or et > shift_end):
                    shift_end = et
        return jsonify({"last_status_time": last_time, "shift_end": shift_end})
    except Exception:
        log.exception("checklist_advisor_info error")
        return jsonify({"last_status_time": "", "shift_end": ""})

# Shared checklist state — cached briefly so page polling doesn't hammer
# Sheets. State is keyed by date; only today's rows are read/written.
_checklist_cache = {"date": None, "data": None, "ts": 0}
_CHECKLIST_TTL = 30  # increased from 20s

def _est_today_str():
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")

@app.route('/api/checklist_state')
def api_checklist_state():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if checklist_sheet is None:
        return jsonify({"ticks": {}, "activity": [], "sheet": False})
    import time
    today = _est_today_str()
    now = time.time()
    if (_checklist_cache["data"] is not None and _checklist_cache["date"] == today
            and now - _checklist_cache["ts"] < _CHECKLIST_TTL):
        return jsonify(_checklist_cache["data"])
    try:
        rows = checklist_sheet.get_all_values()
        ticks, activity = {}, []
        for r in rows[1:]:
            if len(r) < 7 or (r[0] or "").strip() != today: continue
            kind = (r[1] or "").strip()
            if kind == "tick":
                # Append-only trail: rows are in chronological order, so the
                # last row seen for an item_id is its current state.
                # cols: date,kind,item_id,task,value,who,updated_at
                ticks[r[2]] = {"done": (r[4] == "1"), "who": r[5], "time": r[6]}
            elif kind == "activity":
                # value packs the activity payload as JSON (col E)
                try:    payload = _json.loads(r[4]) if r[4] else {}
                except Exception: payload = {}
                payload["id"] = r[2]; payload["who"] = r[5]; payload["at"] = r[6]
                activity.append(payload)
        data = {"ticks": ticks, "activity": activity, "sheet": True, "date": today}
        _checklist_cache.update({"date": today, "data": data, "ts": now})
        del rows
        gc.collect()
        return jsonify(data)
    except Exception:
        log.exception("checklist_state read error")
        return jsonify({"ticks": {}, "activity": [], "sheet": True, "error": True})

def _checklist_find_row(today, kind, item_id):
    """Return the 1-based row index of an existing state row, or None."""
    rows = checklist_sheet.get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if (len(r) >= 3 and (r[0] or "").strip() == today
                and (r[1] or "").strip() == kind and (r[2] or "").strip() == item_id):
            return i
    return None

@app.route('/api/checklist_tick', methods=['POST'])
def api_checklist_tick():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if checklist_sheet is None:
        return jsonify({"error": "Checklist sheet unavailable"}), 500
    try:
        p       = request.get_json(silent=True) or {}
        item_id = (p.get("item_id") or "").strip()
        task    = (p.get("task") or "").strip()
        done    = "1" if p.get("done") else "0"
        if not item_id: return jsonify({"error": "Missing item"}), 400
        today = _est_today_str()
        who   = session.get('user_email', 'WFM')
        stamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M")
        # Append-only audit trail: every check AND uncheck is its own row.
        # The read picks the LATEST row per item to know current state, so
        # the full who/when history stays in the sheet.
        # cols: date,kind,item_id,task,value,who,updated_at
        safe_append_row(checklist_sheet, [today, "tick", item_id, task, done, who, stamp])
        _checklist_cache["ts"] = 0   # invalidate so next read is fresh
        return jsonify({"success": True, "who": who, "time": stamp})
    except Exception:
        log.exception("checklist_tick error")
        return jsonify({"error": "Save failed"}), 500

@app.route('/api/checklist_activity', methods=['POST'])
def api_checklist_activity():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    if checklist_sheet is None:
        return jsonify({"error": "Checklist sheet unavailable"}), 500
    try:
        p     = request.get_json(silent=True) or {}
        action = (p.get("action") or "add").strip()
        today = _est_today_str()
        who   = session.get('user_email', 'WFM')
        stamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M")
        if action == "delete":
            aid = (p.get("id") or "").strip()
            row = _checklist_find_row(today, "activity", aid)
            if row: checklist_sheet.delete_rows(row)
            _checklist_cache["ts"] = 0
            return jsonify({"success": True})

        if action == "update":
            aid = (p.get("id") or "").strip()
            row = _checklist_find_row(today, "activity", aid)
            if not row:
                return jsonify({"error": "Entry not found"}), 404
            payload = {
                "type":         (p.get("type")         or "").strip(),
                "detail":       (p.get("detail")        or "").strip(),
                "advisor":      (p.get("advisor")       or "").strip(),
                "tl":           (p.get("tl")            or "").strip(),
                "lob":          (p.get("lob")           or "").strip(),
                "assisting_lob":(p.get("assisting_lob") or "").strip(),
                "who_involved": (p.get("who_involved")  or "").strip(),
                "from":         (p.get("from")          or "").strip(),
                "to":           (p.get("to")            or "").strip(),
            }
            # Update value column (col 5) and who/stamp (cols 6,7)
            checklist_sheet.update_cell(row, 5, _json.dumps(payload))
            checklist_sheet.update_cell(row, 6, who)
            checklist_sheet.update_cell(row, 7, stamp)
            _checklist_cache["ts"] = 0
            return jsonify({"success": True, "id": aid, "who": who, "at": stamp})
        # add
        payload = {
            "type":         (p.get("type")         or "").strip(),
            "detail":       (p.get("detail")        or "").strip(),
            # structured advisor fields (AHOD/VTO)
            "advisor":      (p.get("advisor")       or "").strip(),
            "tl":           (p.get("tl")            or "").strip(),
            "lob":          (p.get("lob")           or "").strip(),
            "assisting_lob":(p.get("assisting_lob") or "").strip(),
            # legacy free-text (Note type)
            "who_involved": (p.get("who_involved")  or "").strip(),
            "from":         (p.get("from")          or "").strip(),
            "to":           (p.get("to")            or "").strip(),
        }
        aid = "act_" + datetime.now(ZoneInfo(TIMEZONE)).strftime("%H%M%S%f")[:-3]
        # cols: date,kind,item_id(aid),task(blank for activity),value(JSON),who,updated_at
        safe_append_row(checklist_sheet,
                        [today, "activity", aid, "", _json.dumps(payload), who, stamp])
        _checklist_cache["ts"] = 0
        return jsonify({"success": True, "id": aid, "who": who, "at": stamp})
    except Exception:
        log.exception("checklist_activity error")
        return jsonify({"error": "Save failed"}), 500

# ========== STAT DAY REMOVAL CALCULATOR ==========
_roster_term_cache = {"map": None, "ts": 0}

def _extract_termination_map(rows_of_dicts):
    """Build {dbid: termination_date | 'inactive'} from employee-database rows.
    Tolerant header matching. Understands both styles:
      - date columns (TERMINATION_DATE, Last_day_worked)
      - status columns (State/Status/Active with values like Inactive/Terminated)
    Ignores blank / 0000-00-00 placeholders and numeric-only status codes."""
    if not rows_of_dicts: return {}
    keymap = {_norm_key(k): k for k in rows_of_dicts[0].keys()}
    id_key   = next((keymap[k] for k in ("dbid", "agent_dbid", "current_id", "employee_id")
                     if k in keymap), None)
    term_key = next((keymap[k] for k in ("termination_date", "termination", "term_date")
                     if k in keymap), None)
    last_key = next((keymap[k] for k in ("last_day_worked", "last_day")
                     if k in keymap), None)
    stat_key = next((keymap[k] for k in ("employment_status", "status", "state", "active",
                                          "employee_status") if k in keymap), None)
    if not id_key or not (term_key or last_key or stat_key): return {}
    INACTIVE_WORDS = ("inactive", "terminated", "term", "termed", "no", "false", "left",
                      "loa", "leave of absence", "leave")
    out = {}
    for r in rows_of_dicts:
        raw_id = r.get(id_key)
        if raw_id in (None, ""): continue
        dbid = str(raw_id).strip()
        if dbid.endswith(".0"): dbid = dbid[:-2]
        found = False
        for key in (term_key, last_key):
            if not key: continue
            v = r.get(key)
            if v in (None, "", "0000-00-00"): continue
            if hasattr(v, "date"): d = v.date()
            else: d = parse_date(str(v).split(".")[0], as_date=True)
            if d and d.year > 1900:
                out[dbid] = d
                found = True
                break
        if not found and stat_key:
            sv = str(r.get(stat_key) or "").strip().lower()
            # textual status only — numeric codes (0/1) are too ambiguous to trust
            if sv and not sv.replace(".", "").isdigit() and \
               any(w in sv for w in INACTIVE_WORDS) and "active" != sv:
                out[dbid] = "inactive"
    return out

def _load_termination_map(emp_file):
    """Terminated-employee lookup: uploaded employee DB wins; otherwise the
    roster Google Sheet (cached 10 min); otherwise empty (no exclusion)."""
    import time
    if emp_file and emp_file.filename:
        rows = _read_tabular_upload(emp_file)
        if rows:
            return _extract_termination_map(rows), "uploaded employee database"
    if _roster_term_cache["map"] is not None and time.time() - _roster_term_cache["ts"] < 180:
        return _roster_term_cache["map"], "employee roster sheet"
    try:
        rdoc, rerr = _get_roster_doc()
        if rdoc:
            ws   = rdoc.worksheet("EMPLOYEES")
            recs = ws.get_all_records()
            m    = _extract_termination_map(recs)
            _roster_term_cache["map"] = m
            _roster_term_cache["ts"]  = time.time()
            if m: return m, "employee roster sheet"
    except Exception:
        log.exception("Roster termination lookup failed")
    return {}, None

def _read_tabular_upload(f):
    """Parse an uploaded .csv or .xlsx into a list of dicts (header row 1)."""
    name = (f.filename or "").lower()
    if name.endswith('.csv'):
        content = f.read().decode('utf-8-sig', errors='replace')
        return list(csv.DictReader(io.StringIO(content)))
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(h or "").strip() for h in next(rows_iter, [])]
        out = []
        for row in rows_iter:
            if row is None: continue
            d = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
            if any(v not in (None, "") for v in d.values()):
                out.append(d)
        wb.close()
        return out
    return None

@app.route('/stat-day', methods=['GET', 'POST'])
def stat_day_calculator():
    auth = _require_auth()
    if auth: return auth
    if request.method == 'GET':
        return render_template('stat_day.html', results=None,
                               stat_date="", error=None, summary=None)
    stat_date_str = (request.form.get('stat_date') or "").strip()
    stat_date     = parse_date(stat_date_str, as_date=True)
    f             = request.files.get('file')
    if not stat_date:
        return render_template('stat_day.html', results=None,
                               stat_date=stat_date_str, summary=None,
                               error="Please pick a valid stat day date.")
    if not f or not f.filename:
        return render_template('stat_day.html', results=None,
                               stat_date=stat_date_str, summary=None,
                               error="Please upload the DV data export (.csv or .xlsx).")
    try:
        raw = _read_tabular_upload(f)
        if raw is None:
            return render_template('stat_day.html', results=None,
                                   stat_date=stat_date_str, summary=None,
                                   error="Unsupported file type — upload .csv or .xlsx.")
        if not raw:
            return render_template('stat_day.html', results=None,
                                   stat_date=stat_date_str, summary=None,
                                   error="The file appears to be empty.")

        # Tolerant column lookup on the first row's keys
        keymap = {_norm_key(k): k for k in raw[0].keys()}
        def get(r, *names):
            for n in names:
                k = keymap.get(_norm_key(n))
                if k is not None:
                    v = r.get(k)
                    return "" if v is None else str(v).strip()
            return ""
        def num(r, *names):
            try:    return float(get(r, *names) or 0)
            except: return 0.0

        needed = ("event_date", "agent_dbid", "sched_time")
        missing = [n for n in needed if _norm_key(n) not in keymap]
        if missing:
            return render_template('stat_day.html', results=None,
                                   stat_date=stat_date_str, summary=None,
                                   error=f"File is missing expected columns: {', '.join(missing)}. "
                                         "Upload the raw DV daily export.")

        # Group rows per employee
        emps = {}
        min_d = max_d = None
        for r in raw:
            d = parse_date(get(r, "event_date"), as_date=True)
            if not d:
                # openpyxl gives datetimes directly
                v = r.get(keymap.get("event_date"))
                if hasattr(v, "date"): d = v.date()
            if not d: continue
            dbid = get(r, "agent_dbid") or get(r, "user_id")
            if not dbid: continue
            min_d = d if not min_d or d < min_d else min_d
            max_d = d if not max_d or d > max_d else max_d
            e = emps.setdefault(dbid, {"name": "", "tl": "", "lob": "", "days": {}})
            if not e["name"]: e["name"] = get(r, "agent_name")
            if not e["tl"]:   e["tl"]   = get(r, "supervisor")
            if not e["lob"]:  e["lob"]  = get(r, "LOB", "lobName")
            e["days"][d] = {
                "sched_hrs": round(num(r, "SCHED_TIME") / 3600, 2),
                "absent":    num(r, "absent") >= 1,
                "late_min":  round(num(r, "LATE") / 60, 1),
                "el_min":    round(num(r, "Leave_Early") / 60, 1),
            }

        def fmt_leg(d, day):
            if not day: return None
            return {"date": d.strftime("%b %d"), "hrs": day["sched_hrs"],
                    "absent": day["absent"], "late": day["late_min"],
                    "el": day["el_min"]}

        term_map, term_source = _load_termination_map(request.files.get('emp_file'))

        results, excluded = [], []
        for dbid, e in emps.items():
            # ── Terminated cross-reference ──────────────────────────
            term_d = term_map.get(str(dbid).strip())
            if term_d == "inactive":
                sv_check = ""
                # Try to surface the actual status for clarity
                excluded.append({"dvid": dbid, "name": e["name"], "tl": e["tl"],
                                 "term": "Inactive / LOA"})
                continue
            if term_d and term_d <= stat_date:
                excluded.append({"dvid": dbid, "name": e["name"], "tl": e["tl"],
                                 "term": term_d.strftime("%b %d, %Y")})
                continue
            days = e["days"]

            # Exclude agents on LOA — check LOB field or zero scheduled hours across all days
            lob_val = (e.get("lob") or "").strip().upper()
            all_zero_sched = all(v["sched_hrs"] == 0 for v in days.values())
            if "LOA" in lob_val or all_zero_sched:
                excluded.append({"dvid": dbid, "name": e["name"], "tl": e["tl"],
                                 "term": "LOA / No scheduled hours"})
                continue
            before_d = max((d for d, v in days.items()
                            if d < stat_date and v["sched_hrs"] > 0), default=None)
            after_d  = min((d for d, v in days.items()
                            if d > stat_date and v["sched_hrs"] > 0), default=None)
            stat_e   = days.get(stat_date)
            sched_on_stat = bool(stat_e and stat_e["sched_hrs"] > 0)

            before = fmt_leg(before_d, days.get(before_d)) if before_d else None
            after  = fmt_leg(after_d,  days.get(after_d))  if after_d  else None
            stat_l = fmt_leg(stat_date, stat_e) if sched_on_stat else None

            notes = []
            remove = False
            if before and before["absent"]:
                remove = True; notes.append(f"Absent last shift before stat ({before['date']})")
            if stat_l and stat_l["absent"]:
                remove = True; notes.append("No-show on stat day")
            if after and after["absent"]:
                remove = True; notes.append(f"Absent first shift after stat ({after['date']})")
            # Late or early leave > half the scheduled shift = removal
            for leg, lbl in ((before, "before"), (stat_l, "stat day"), (after, "after")):
                if leg and not leg["absent"]:
                    half_min = round(leg["hrs"] * 60 / 2, 1)
                    if leg["late"] > 2:
                        msg = f"Late {leg['late']:g}m {lbl}"
                        if half_min > 0 and leg["late"] >= half_min:
                            remove = True
                            msg += f" (>{half_min:g}m = more than half shift)"
                        notes.append(msg)
                    if leg["el"] > 2:
                        msg = f"Left early {leg['el']:g}m {lbl}"
                        if half_min > 0 and leg["el"] >= half_min:
                            remove = True
                            msg += f" (>{half_min:g}m = more than half shift)"
                        notes.append(msg)
            if not before: notes.append("No scheduled shift before stat in file")
            if not after:  notes.append("No scheduled shift after stat in file")
            if term_d:  # terminated after the stat — keep, but flag for payroll context
                notes.insert(0, f"Terminated {term_d.strftime('%b %d')} (after stat)")

            # Skip people with no schedule anywhere near the stat
            if not before and not after and not sched_on_stat:
                continue
            results.append({"dvid": dbid, "name": e["name"], "tl": e["tl"],
                            "lob": e["lob"], "before": before, "stat": stat_l,
                            "sched_on_stat": sched_on_stat, "after": after,
                            "remove": remove, "notes": "; ".join(notes)})

        results.sort(key=lambda x: (not x["remove"], x["tl"], x["name"]))
        excluded.sort(key=lambda x: (x["tl"], x["name"]))
        summary = {"total": len(results),
                   "flagged": sum(1 for x in results if x["remove"]),
                   "excluded": len(excluded),
                   "term_source": term_source,
                   "range": f"{min_d.strftime('%b %d')} – {max_d.strftime('%b %d, %Y')}" if min_d else "",
                   "stat_pretty": stat_date.strftime("%A, %B %d, %Y")}
        # free the large parsed structures before rendering
        emps.clear()
        if isinstance(term_map, dict): term_map.clear()
        gc.collect()
        return render_template('stat_day.html', results=results, excluded=excluded,
                               stat_date=stat_date_str, summary=summary, error=None)
    except Exception:
        log.exception("Stat day calculator error")
        return render_template('stat_day.html', results=None,
                               stat_date=stat_date_str, summary=None,
                               error="Something went wrong reading that file — check it's the raw DV export and try again.")

@app.route('/stat-day/export', methods=['POST'])
def stat_day_export():
    """Build a styled Excel workbook from the calculated stat-day results."""
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        from flask import send_file
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        payload = request.get_json(silent=True) or {}
        results = payload.get('results') or []
        stat_pretty = payload.get('stat_pretty') or ""

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stat Pay Removal"

        header_fill = PatternFill('solid', start_color='1565C0')
        header_font = Font(bold=True, color='FFFFFF', name='Segoe UI', size=10)
        flag_fill   = PatternFill('solid', start_color='FDECEA')
        red_font    = Font(bold=True, color='B71C1C', name='Segoe UI', size=10)
        base_font   = Font(name='Segoe UI', size=10)
        thin        = Border(*[Side(style='thin', color='D0D0D0')] * 4)

        ws['A1'] = f"Stat Pay Removal List — {stat_pretty}"
        ws['A1'].font = Font(bold=True, size=13, name='Segoe UI')
        ws.merge_cells('A1:N1')

        headers = ["DV ID", "Team Lead", "Name", "LOB",
                   "Shift Before", "Hrs", "Status Before",
                   "Stat Day Sched (hrs)", "Status Stat Day",
                   "Shift After", "Hrs", "Status After",
                   "Remove Stat Pay", "Notes"]
        ws.append([])
        ws.append(headers)
        for c in ws[3]:
            c.fill = header_fill; c.font = header_font
            c.alignment = Alignment(horizontal='center'); c.border = thin

        def leg(l, key):
            if not l: return "Not in file" if key == "absent" else "—"
            if key == "date":   return l.get("date") or "—"
            if key == "hrs":    return l.get("hrs")
            if key == "absent": return "Absent" if l.get("absent") else "Present"
            return "—"

        for r in results:
            row = [r.get("dvid"), r.get("tl"), r.get("name"), r.get("lob"),
                   leg(r.get("before"), "date"), leg(r.get("before"), "hrs"),
                   leg(r.get("before"), "absent"),
                   (r.get("stat") or {}).get("hrs") if r.get("sched_on_stat") else "Off",
                   (leg(r.get("stat"), "absent") if r.get("sched_on_stat") else "Off"),
                   leg(r.get("after"), "date"), leg(r.get("after"), "hrs"),
                   leg(r.get("after"), "absent"),
                   "REMOVE" if r.get("remove") else "Keep",
                   r.get("notes", "")]
            ws.append(row)
            xl_row = ws.max_row
            for c in ws[xl_row]:
                c.font = base_font; c.border = thin
            if r.get("remove"):
                for c in ws[xl_row]:
                    c.fill = flag_fill
                ws.cell(row=xl_row, column=13).font = red_font
            for col in (7, 9, 12):
                cell = ws.cell(row=xl_row, column=col)
                cell.alignment = Alignment(horizontal='center')
                if cell.value == "Absent": cell.font = red_font

        widths = [8, 20, 24, 16, 12, 6, 13, 17, 11, 12, 6, 12, 15, 55]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        ws.freeze_panes = "A4"
        ws.auto_filter.ref = f"A3:N{ws.max_row}"

        buf = io.BytesIO()
        wb.save(buf)
        wb.close()   # release the in-memory workbook before streaming
        buf.seek(0)
        safe_date = re.sub(r'[^A-Za-z0-9]+', '_', stat_pretty).strip('_') or "stat_day"
        return send_file(buf, as_attachment=True,
                         download_name=f"Stat_Day_Removal_{safe_date}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception:
        log.exception("Stat day export error")
        return jsonify({"error": "Export failed"}), 500

# ========== PEOPLEWARE INTERVAL CSV CONVERTER ==========
class _HtmlTableGrabber:
    """Minimal stdlib HTML table extractor (no pandas/lxml dependency)."""
    def __init__(self):
        from html.parser import HTMLParser
        class _P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tables, self._t, self._r, self._cell = [], None, None, None
            def handle_starttag(self, tag, attrs):
                if tag == 'table': self._t = []
                elif tag == 'tr' and self._t is not None: self._r = []
                elif tag in ('td', 'th') and self._r is not None: self._cell = []
            def handle_endtag(self, tag):
                if tag == 'table' and self._t is not None:
                    self.tables.append(self._t); self._t = None
                elif tag == 'tr' and self._r is not None:
                    self._t.append(self._r); self._r = None
                elif tag in ('td', 'th') and self._cell is not None:
                    self._r.append(' '.join(''.join(self._cell).split())); self._cell = None
            def handle_data(self, d):
                if self._cell is not None: self._cell.append(d)
        self._p = _P()
    def parse(self, html):
        self._p.feed(html)
        return self._p.tables

def _parse_interval_report(f):
    """Parse a WFM Daily Interval email (.msg or saved .html) into
    (report_date, sections). Each section: {lob, rows:[[hh:mm:ss, offered,
    handled, aht_seconds], ...]}. Handles both column dialects (NCH vs
    Answered) and both interval formats (09:00 vs 15:00:00)."""
    name = (f.filename or "").lower()
    data = f.read()
    body_text, subject, html = "", "", None
    if name.endswith('.msg'):
        try:
            import extract_msg
        except ImportError:
            return None, None, ("The .msg parser isn't installed on the server — "
                                "add 'extract-msg' to requirements.txt and redeploy, "
                                "or save the email as HTML and upload that instead.")
        msg = extract_msg.Message(io.BytesIO(data))
        html = msg.htmlBody
        if isinstance(html, bytes): html = html.decode('utf-8', errors='replace')
        body_text = msg.body or ""
        subject   = msg.subject or ""
    elif name.endswith('.html') or name.endswith('.htm') or name.endswith('.mht'):
        html = data.decode('utf-8', errors='replace')
        body_text = html
    else:
        return None, None, "Unsupported file type — upload the .msg email or a saved .html copy."
    if not html:
        return None, None, "Couldn't find an HTML body in that email."

    m = (re.search(r'Date\s*:\s*(\d{4}-\d{2}-\d{2})', body_text) or
         re.search(r'(\d{4}-\d{2}-\d{2})', subject) or
         re.search(r'Date\s*:\s*(\d{4}-\d{2}-\d{2})', html))
    report_date = m.group(1) if m else ""

    def _n(v):
        v = (v or "").replace(',', '').replace('%', '').strip()
        try: return int(float(v))
        except Exception: return 0

    sections = []
    for t in _HtmlTableGrabber().parse(html):
        rows = [r for r in t if any(c for c in r)]
        if not rows: continue
        lob = next((c for c in rows[0] if c), "")
        hdr = next((r for r in rows
                    if any((c or "").strip().lower() == "interval" for c in r)), None)
        if not hdr or not lob: continue
        idx = {(c or "").strip().lower(): i for i, c in enumerate(hdr)}
        i_int = idx.get("interval")
        i_off = idx.get("offered")
        i_h   = idx.get("nch", idx.get("answered"))
        i_aht = idx.get("aht")
        if None in (i_int, i_off, i_h): continue
        out = []
        for r in rows:
            first = (r[0] or "").strip() if r else ""
            mt = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?$', first)
            if not mt: continue
            hh, mm, ampm = int(mt.group(1)), mt.group(2), (mt.group(4) or "").upper()
            if ampm == "PM" and hh != 12: hh += 12
            if ampm == "AM" and hh == 12: hh = 0
            time_s = f"{hh:02d}:{mm}:00"
            out.append([time_s,
                        _n(r[i_off] if i_off < len(r) else ""),
                        _n(r[i_h]   if i_h   < len(r) else ""),
                        _n(r[i_aht] if i_aht is not None and i_aht < len(r) else "")])
        if out:
            sections.append({"lob": lob, "rows": out,
                             "offered": sum(x[1] for x in out),
                             "handled": sum(x[2] for x in out)})
    if not sections:
        return None, None, ("No interval tables found — make sure this is the "
                            "WFM Daily Interval report email.")
    return report_date, sections, None

def _parse_callstatus_csv(f):
    """Parse a raw Call Potential call-status export (track_callcenter_tasks)
    into the same sections structure as the email path, using the exact
    counting rules from rtm.py: queue→LOB via QUEUE_MAP, CST→EST (+1h),
    offer time = date_created − queue_time bucketed to 30 min, unique
    log_id per LOB/day (first-seen record), answered = not abandoned and
    not rolled. AHT is 0 — the raw export has no talk-time column.
    Rows carry their own date (exports can span multiple days)."""
    import rtm as _rtm
    content = f.read().decode('utf-8-sig', errors='replace')
    reader  = csv.DictReader(io.StringIO(content))
    interval_data = {}   # (group, date_str, bucket_str) → {offered, handled}
    seen_logs     = {}   # (group, date_str) → set of log_ids
    # group is either the base LOB ("SS Sales") or a language split
    # ("SS Sales EN"); each call is counted into BOTH so the output offers
    # combined and per-language sections side by side.
    for r in reader:
        dc = (r.get("date_created") or "").strip()
        dt_cst = _rtm._parse_datetime_str(dc)
        if not dt_cst: continue
        dt_est = _rtm._cp_to_est(dt_cst)
        raw_qt = r.get("queue_time", "")
        try:    qt = float(raw_qt) if str(raw_qt).strip() != "" else 0
        except Exception: qt = 0
        offer = dt_est - timedelta(seconds=qt)
        if offer.date() != dt_est.date():
            offer = dt_est   # clamp cross-midnight drift, matching rtm.py
        try:    qid = int(r.get("queue_id", 0) or 0)
        except Exception: qid = 0
        qinfo = _rtm.QUEUE_MAP.get(qid)
        if not qinfo: continue
        lob, lang = qinfo[0], (qinfo[1] or "").strip()
        log_id = str(r.get("log_id", "") or "").strip()
        if not log_id: continue
        date_str = offer.strftime("%Y-%m-%d")
        is_aband  = 1 if str(r.get("is_abandoned", "")).strip()   in ("1", "1.0") else 0
        is_rolled = 1 if str(r.get("is_rolled_over", "")).strip() in ("1", "1.0") else 0
        bucket = offer.replace(minute=(offer.minute // 30) * 30,
                               second=0, microsecond=0).strftime("%H:%M:%S")

        # Count into the combined LOB group and, when a language is known,
        # into the language-split group too. log_id dedup is per group/day.
        groups = [lob]
        if lang:
            groups.append(f"{lob} {lang}")
        for grp in groups:
            skey = (grp, date_str)
            if skey not in seen_logs: seen_logs[skey] = set()
            if log_id in seen_logs[skey]: continue   # first-seen record wins
            seen_logs[skey].add(log_id)
            key = (grp, date_str, bucket)
            if key not in interval_data:
                interval_data[key] = {"offered": 0, "handled": 0}
            interval_data[key]["offered"] += 1
            if not is_aband and not is_rolled:
                interval_data[key]["handled"] += 1

    if not interval_data:
        return None, None, ("No recognizable call data found — make sure this is "
                            "the raw call status export (track_callcenter_tasks) "
                            "with log_id / queue_id / date_created columns.")
    # Assemble sections per group, rows sorted by date then time, each row
    # carrying its own date: [time, offered, handled, aht, date]
    by_grp = {}
    for (grp, d, t), v in sorted(interval_data.items()):
        by_grp.setdefault(grp, []).append([t, v["offered"], v["handled"], 0, d])
    # Order: each base LOB followed by its EN/FR splits
    def _sort_key(name):
        base = name.rsplit(" ", 1)[0] if name.endswith((" EN", " FR")) else name
        rank = 0 if base == name else (1 if name.endswith(" EN") else 2)
        return (base, rank)
    sections = [{"lob": grp, "rows": rows,
                 "offered": sum(x[1] for x in rows),
                 "handled": sum(x[2] for x in rows)}
                for grp, rows in sorted(by_grp.items(), key=lambda kv: _sort_key(kv[0]))]
    dates = sorted({(x[4]) for s in sections for x in s["rows"]})
    default_date = dates[0] if len(dates) == 1 else ""
    # free the large intermediate maps before returning the compact sections
    interval_data.clear(); seen_logs.clear(); by_grp.clear()
    del content
    gc.collect()
    return default_date, sections, None

@app.route('/pw-interval', methods=['GET', 'POST'])
def pw_interval():
    auth = _require_auth()
    if auth: return auth
    if request.method == 'GET':
        return render_template('pw_interval.html', sections=None,
                               report_date="", error=None)
    f = request.files.get('file')
    if not f or not f.filename:
        return render_template('pw_interval.html', sections=None,
                               report_date="", error="Please upload the interval report email (.msg or .html).")
    try:
        name = (f.filename or "").lower()
        if name.endswith('.csv'):
            report_date, sections, err = _parse_callstatus_csv(f)
        else:
            report_date, sections, err = _parse_interval_report(f)
        return render_template('pw_interval.html', sections=sections,
                               report_date=report_date or "", error=err)
    except Exception:
        log.exception("PW interval parse error")
        return render_template('pw_interval.html', sections=None, report_date="",
                               error="Something went wrong reading that file — check it's the interval report email.")

# ========== AUTO TICKET ==========
@app.route('/auto_ticket', methods=['POST'])
def auto_ticket():
    if request.headers.get('X-API-Key', '') != os.environ.get('AUTO_TICKET_KEY', ''):
        return jsonify({"error": "Unauthorized"}), 401
    if sheet is None: return jsonify({"error": "Not connected"}), 500
    try:
        data         = request.get_json(silent=True) or {}
        sender       = (data.get('sender')  or 'Unknown').strip()
        subject      = (data.get('subject') or 'No Subject').strip()
        body         = (data.get('body')    or '').strip()[:500]
        timestamp    = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
        timestamp_id = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y%m%d%H%M%S")
        ticket_id    = f"TXT-{timestamp_id}"
        all_values   = sheet.get_all_values()
        headers      = all_values[0] if all_values else []
        row_data     = {h: "" for h in headers}
        row_data["Submitted At"] = timestamp
        row_data["Submitted By"] = sender
        row_data["Status"]       = "Open"
        row_data["ticket_id"]    = ticket_id
        for h in headers:
            hl = h.lower().replace(" ", "_")
            if any(k in hl for k in ("advisor", "name", "requestor")):
                row_data[h] = sender
            elif any(k in hl for k in ("wfm_request", "request", "subject", "type")):
                row_data[h] = subject
            elif any(k in hl for k in ("note", "comment", "detail", "description", "body")):
                row_data[h] = body
        sheet.append_row([row_data.get(h, "") for h in headers])
        _invalidate_cache()
        return jsonify({"success": True, "ticket_id": ticket_id}), 201
    except Exception as e:
        log.exception("Auto-ticket error")
        return jsonify({"error": str(e)}), 500

# ========== JINJA FILTERS ==========
@app.template_filter('month_label')
def month_label_filter(value):
    try:
        import datetime as _dt
        dt = _dt.datetime.strptime(str(value), "%Y-%m")
        return dt.strftime("%b %Y")
    except Exception:
        return str(value)

@app.template_filter('format_number')
def format_number_filter(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)

# ========== CAPACITY PLANNING ==========
def _get_cap_doc():
    doc, err = cp.get_cap_sheet()
    return doc, err

def _get_roster_doc():
    """
    Open the standalone employee roster Google Sheet
    (populated by the old upload_employee_roster script).
    Key is in ROSTER_SHEET_KEY env var; falls back to CAPACITY_SHEET_KEY.
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope  = ["https://spreadsheets.google.com/feeds",
                  "https://www.googleapis.com/auth/drive"]
        creds  = ServiceAccountCredentials.from_json_keyfile_name(
            os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json"), scope)
        client = gspread.authorize(creds)
        key    = os.environ.get("ROSTER_SHEET_KEY") or os.environ.get("CAPACITY_SHEET_KEY", "")
        if not key:
            return None, "ROSTER_SHEET_KEY not set"
        return client.open_by_key(key), None
    except Exception as e:
        return None, str(e)

def _read_employees():
    """
    Read employees from the roster sheet if ROSTER_SHEET_KEY is set,
    otherwise fall back to the EMPLOYEES tab in the capacity sheet.
    Returns (emp_data, err).
    """
    roster_key = os.environ.get("ROSTER_SHEET_KEY", "")
    if roster_key:
        rdoc, rerr = _get_roster_doc()
        if rdoc:
            try:
                ws   = rdoc.worksheet("EMPLOYEES")
                data = ws.get_all_values()
                if not data or len(data) < 2:
                    return [], "Roster sheet is empty"
                headers = data[0]
                rows    = []
                for row in data[1:]:
                    if not any(row): continue
                    emp = dict(zip(headers, row))
                    rows.append({
                        "status":       emp.get("Status", "").strip(),
                        "deleted":      emp.get("Deleted", "").strip(),
                        "planningUnit": emp.get("Planning Unit", "").strip(),
                        "firstName":    emp.get("First Name", ""),
                        "lastName":     emp.get("Last Name", ""),
                        "employeeId":   emp.get("Employee ID", ""),
                        "skillName":    emp.get("Latest Skill Name", "").strip(),
                        "skillStart":   emp.get("Latest Skill Start", "").strip(),
                        "skillEnd":     emp.get("Latest Skill End", "").strip(),
                        "allSkills":    emp.get("All Skills", "").strip(),
                        "empStart":     emp.get("Start Date", "").strip(),
                        "empEnd":       emp.get("End Date", "").strip(),
                    })
                log.info(f"ROSTER SHEET: {len(rows)} employees loaded")
                return rows, None
            except Exception as e:
                log.warning(f"Roster sheet read failed: {e} — falling back to capacity sheet")
    # Fallback: read from EMPLOYEES tab in capacity sheet
    cap_doc, cap_err = _get_cap_doc()
    if not cap_doc:
        return [], cap_err
    return cp.read_employees_raw(cap_doc)

def _read_capacity_plan_sheet(doc):
    """
    Read CAPACITY PLAN sheet and return (plan_dict, all_months_list).
    Handles the 'Built' timestamp row before the header row.
    """
    plan       = {}
    all_months = []
    try:
        ws       = doc.worksheet("CAPACITY PLAN")
        all_vals = ws.get_all_values()
        log.info(f"CAPACITY PLAN read: {len(all_vals)} rows total")
        if not all_vals:
            return plan, all_months
        # Find header row — first row where col A is "lob"
        header_row_idx = None
        for i, row in enumerate(all_vals):
            if row and str(row[0]).strip().lower() == "lob":
                header_row_idx = i
                break
        log.info(f"CAPACITY PLAN header_row_idx={header_row_idx}")
        if header_row_idx is None:
            return plan, all_months
        headers_row = all_vals[header_row_idx]
        data = [dict(zip(headers_row, r))
                for r in all_vals[header_row_idx + 1:] if any(r)]
        log.info(f"CAPACITY PLAN data rows parsed: {len(data)}")
        for row in data:
            lob_raw = str(row.get("lob", "")).strip()
            lob     = cp.LOB_DISPLAY_NAMES.get(lob_raw, lob_raw)  # apply display names
            month   = str(row.get("month", "")).strip()
            if not lob or not month or lob == "lob":
                continue
            if lob not in plan:
                plan[lob] = {}
            plan[lob][month] = {
                "month":            month,
                "forecasted_calls": float(row.get("forecasted_calls", 0) or 0),
                "calls_answered":   float(row.get("calls_answered",   0) or 0),
                "aht":              float(row.get("aht",            470) or 470),
                "occupancy":        float(row.get("occupancy",       0) or 0),
                "psih_no_shrink":   float(row.get("psih_no_shrink",  0) or 0),
                "psih_with_shrink": float(row.get("psih_with_shrink",0) or 0),
                "fte_required":     float(row.get("fte_required",    0) or 0),
                "actual_hc":        int(  row.get("actual_hc",       0) or 0),
                "req_vs_actual":    float(row.get("req_vs_actual",   0) or 0),
                "shrinkage":        float(row.get("shrinkage",      0.3) or 0.3),
                "peak_agents":      float(row.get("peak_agents",     0) or 0),
                "avg_agents":       float(row.get("avg_agents",      0) or 0),
                "interval_count":   int(  row.get("interval_count",  0) or 0),
            }
        all_months = sorted(set(m for lob_data in plan.values() for m in lob_data))
        log.info(f"CAPACITY PLAN loaded: {len(plan)} LOBs, {len(all_months)} months")
    except Exception as e:
        log.warning(f"_read_capacity_plan_sheet error: {e}")
    return plan, all_months

@app.route('/capacity')
def capacity_control():
    auth = _require_auth()
    if auth: return auth
    doc, err = _get_cap_doc()
    status   = cp.get_refresh_status(doc) if doc else {
        "FORECAST RAW":     {"rows": 0, "has_data": False},
        "REQUIREMENTS RAW": {"rows": 0, "has_data": False},
        "EMPLOYEES":        {"rows": 0, "has_data": False},
        "AHT RAW":          {"rows": 0, "has_data": False},
    }
    # Override EMPLOYEES count with roster sheet (the real source)
    emp_data, _ = _read_employees()
    status["EMPLOYEES"] = {
        "rows":     len(emp_data),
        "has_data": len(emp_data) > 0,
    }
    import datetime as _dt
    today = _dt.date.today()
    return render_template('capacity_control.html',
        token_ok      = bool(cp.get_token()),
        sheet_ok      = doc is not None,
        utc_offset    = cp.get_utc_offset(),
        status        = status,
        default_start = today.replace(day=1).strftime("%Y-%m-%d"),
        default_end   = today.strftime("%Y-%m-%d"),
        current_year  = cp.current_year(),
        message       = request.args.get("message", ""),
        success       = request.args.get("success", "1") == "1",
    )

@app.route('/capacity/refresh', methods=['POST'])
def capacity_refresh():
    auth = _require_auth()
    if auth: return auth
    action     = request.form.get("action", "")
    start_date = request.form.get("start_date", "")
    end_date   = request.form.get("end_date", "")
    append     = request.form.get("append", "yes") == "yes"
    shrinkage  = float(request.form.get("shrinkage", 30)) / 100
    doc, err   = _get_cap_doc()
    if not doc:
        return redirect(url_for('capacity_control') + f"?message=Sheet+error:+{err}&success=0")
    token = cp.get_token()
    if not token and action != "build_plan":
        return redirect(url_for('capacity_control') + "?message=API+token+not+configured&success=0")
    try:
        if action == "employees":
            employees, err = cp.fetch_employees(token)
            if err:
                return redirect(url_for('capacity_control') + f"?message=Employees+error:+{err}&success=0")
            ok, err = cp.write_employees_to_sheet(doc, employees)
            msg = f"Employees pulled: {len(employees)} records" if ok else f"Write error: {err}"
            return redirect(url_for('capacity_control') + f"?message={msg}&success={'1' if ok else '0'}")

        elif action in ("forecast", "forecast_year"):
            if action == "forecast_year":
                start_date, end_date = cp.year_range(cp.current_year())
            dates  = cp.date_range(start_date, end_date)
            utcoff = cp.get_utc_offset()
            total  = 0
            first  = True
            for wid, name in cp.ACTIVE_WORKLOADS:
                for d in dates:
                    intervals, err = cp.fetch_forecast_day(wid, d, utcoff, token)
                    if intervals:
                        cp.write_forecast_to_sheet(doc, name, intervals, append=(append and not first))
                        total += len(intervals)
                        first  = False
            msg = f"Forecast pulled: {total} intervals across {len(dates)} days"
            return redirect(url_for('capacity_control') + f"?message={msg}&success=1")

        elif action in ("requirements", "requirements_year"):
            if action == "requirements_year":
                start_date, end_date = cp.year_range(cp.current_year())
            dates      = cp.date_range(start_date, end_date)
            units, err = cp.fetch_planning_units(token)
            if err:
                return redirect(url_for('capacity_control') + f"?message=Planning+units+error:+{err}&success=0")
            total = 0
            first = True
            for pu in (units or []):
                pu_id   = pu.get("planning_unit_id", pu.get("id", ""))
                pu_name = pu.get("name", "")
                if not pu_id or pu_name in cp.EXCLUDED_WORKLOADS: continue
                for d in dates:
                    intervals, err = cp.fetch_requirements_day(pu_id, d, token)
                    if intervals:
                        cp.write_requirements_to_sheet(doc, pu_name, intervals, append=(append and not first))
                        total += len(intervals)
                        first  = False
            msg = f"Requirements pulled: {total} intervals"
            return redirect(url_for('capacity_control') + f"?message={msg}&success=1")

        elif action == "build_plan":
            fc_data, fc_err   = cp.read_forecast_raw(doc)
            req_data, req_err = cp.read_requirements_raw(doc)
            emp_data, emp_err = _read_employees()
            aht_data          = cp.read_aht_raw(doc)
            log.info(f"BUILD PLAN: fc={len(fc_data)} rows err={fc_err}")
            log.info(f"BUILD PLAN: req={len(req_data)} rows err={req_err}")
            log.info(f"BUILD PLAN: emp={len(emp_data)} rows err={emp_err}")
            log.info(f"BUILD PLAN: aht={len(aht_data)} LOBs with AHT data")
            if fc_data:  log.info(f"BUILD PLAN first fc row: {fc_data[0]}")
            if req_data: log.info(f"BUILD PLAN first req row: {req_data[0]}")
            plan, months = cp.build_capacity_plan(
                fc_data, req_data, emp_data,
                shrinkage=shrinkage,
                occupancy=float(request.form.get("occupancy", 85)) / 100,
                answer_rate=float(request.form.get("answer_rate", 92)) / 100,
                aht_data=aht_data)
            log.info(f"BUILD PLAN result: {len(plan)} LOBs x {len(months)} months")

            # Write to CAPACITY PLAN sheet
            try:
                import datetime as _dt
                ws = doc.worksheet("CAPACITY PLAN")
                ws.clear()
                col_headers = ["lob", "month", "forecasted_calls", "calls_answered",
                               "aht", "occupancy", "psih_no_shrink", "psih_with_shrink",
                               "fte_required", "actual_hc", "req_vs_actual",
                               "shrinkage", "peak_agents", "avg_agents", "interval_count"]
                all_rows = [
                    ["Built", _dt.datetime.now().strftime("%Y-%m-%d %H:%M")],
                    col_headers,
                ]
                for lob, mdata in plan.items():
                    for month, vals in mdata.items():
                        all_rows.append([lob, month,
                            vals["forecasted_calls"], vals["calls_answered"],
                            vals["aht"], vals["occupancy"],
                            vals["psih_no_shrink"], vals["psih_with_shrink"],
                            vals["fte_required"], vals["actual_hc"],
                            vals["req_vs_actual"], vals["shrinkage"],
                            vals["peak_agents"], vals["avg_agents"],
                            vals["interval_count"]])
                # Write all rows starting from A1 directly — avoids safe_append_row
                # offset bug where get_all_values() returns stale cache after clear()
                ws.update("A1", all_rows, value_input_option="RAW")
                log.info(f"CAPACITY PLAN sheet written: {len(all_rows)-2} data rows")
            except Exception as e:
                log.warning(f"Could not write capacity plan sheet: {e}")

            msg = f"Capacity plan built: {len(plan)} LOBs x {len(months)} months"
            return redirect(url_for('capacity_control') + f"?message={msg}&success=1")

    except Exception as e:
        log.exception("Capacity refresh error")
        return redirect(url_for('capacity_control') +
                        f"?message=Error:+{str(e)[:100]}&success=0")

    return redirect(url_for('capacity_control'))

@app.route('/capacity/plan', methods=['GET', 'POST'])
def capacity_plan_view():
    auth = _require_auth()
    if auth: return auth
    doc, err = _get_cap_doc()

    shrinkage_pct   = int(request.values.get("shrinkage",    30))
    answer_rate_pct = int(request.values.get("answer_rate",  92))
    occupancy_pct   = int(request.values.get("occupancy",    85))
    selected_lobs   = request.values.getlist("lobs")
    legacy_lob      = request.values.get("lob", "").strip()
    if legacy_lob and not selected_lobs:
        selected_lobs = [legacy_lob]

    # If parameters differ from defaults or it's a POST, recalculate on the fly
    # so the user sees updated numbers immediately without a full rebuild
    params_changed = (request.method == 'POST' or
                      shrinkage_pct   != 30 or
                      answer_rate_pct != 92 or
                      occupancy_pct   != 85)

    if doc and params_changed:
        # Recalculate from raw sheets using the new parameters
        fc_data,  _  = cp.read_forecast_raw(doc)
        req_data, _  = cp.read_requirements_raw(doc)
        emp_data, _  = _read_employees()
        aht_data     = cp.read_aht_raw(doc)
        plan, all_months = cp.build_capacity_plan(
            fc_data, req_data, emp_data,
            shrinkage   = shrinkage_pct   / 100,
            occupancy   = occupancy_pct   / 100,
            answer_rate = answer_rate_pct / 100,
            aht_data    = aht_data,
        )
    elif doc:
        plan, all_months = _read_capacity_plan_sheet(doc)
    else:
        plan, all_months = {}, []

    all_lobs = sorted(plan.keys())
    if selected_lobs:
        plan = {k: v for k, v in plan.items() if k in selected_lobs}

    log.info(f"capacity_plan_view: {len(all_lobs)} LOBs, params_changed={params_changed}")

    return render_template('capacity_plan.html',
        plan               = plan,
        all_months         = all_months,
        all_lobs_available = all_lobs,
        filter_lob         = legacy_lob,
        selected_lobs      = selected_lobs,
        shrinkage_pct      = shrinkage_pct,
        answer_rate_pct    = answer_rate_pct,
        occupancy_pct      = occupancy_pct,
        lob_count          = len(plan),
        month_count        = len(all_months),
    )

@app.route('/capacity/summary')
def capacity_summary_view():
    auth = _require_auth()
    if auth: return auth

    filter_month  = request.args.get("month", "")
    filter_months = request.args.getlist("months")  # multi-select
    search        = request.args.get("search", "").strip().lower()

    doc, _     = _get_cap_doc()
    plan       = {}
    all_months = []
    if doc:
        plan, all_months = _read_capacity_plan_sheet(doc)

    all_lobs_available = sorted(plan.keys())
    selected_lobs      = request.args.getlist("lobs")

    # Multi-month takes priority over single month
    if filter_months:
        summary_rows = cp.build_capacity_summary(plan, all_months, filter_months=filter_months)
    else:
        summary_rows = cp.build_capacity_summary(plan, all_months, filter_month or None)

    if selected_lobs:
        summary_rows = [r for r in summary_rows if r["lob"] in selected_lobs]
    if search:
        summary_rows = [r for r in summary_rows if search in r["lob"].lower()]

    return render_template('capacity_summary.html',
        summary_rows       = summary_rows,
        available_months   = all_months,
        filter_month       = filter_month,
        filter_months      = filter_months,
        search             = request.args.get("search", ""),
        all_lobs_available = all_lobs_available,
        selected_lobs      = selected_lobs,
        plan               = plan,
        all_months         = all_months,
    )

@app.route('/capacity/headcount')
def capacity_headcount_view():
    auth = _require_auth()
    if auth: return auth
    doc, _   = _get_cap_doc()
    hc_rows  = []
    employees = []
    last_ref = ""
    if doc:
        emp_data, err = _read_employees()
        if emp_data:
            import datetime as _dt
            last_ref  = _dt.datetime.now().strftime("%Y-%m-%d")
            hc_rows   = cp.build_headcount_view(emp_data)
            # Pass active + LOA employees to the directory
            employees = sorted(
                [e for e in emp_data
                 if str(e.get("deleted","")).strip().lower() not in ("true","yes","1","deleted")],
                key=lambda e: (e.get("lastName",""), e.get("firstName",""))
            )
    search = request.args.get("search", "").strip().lower()
    if search:
        hc_rows = [r for r in hc_rows if search in r["planning_unit"].lower()]
    return render_template('capacity_headcount.html',
        hc_rows        = hc_rows,
        employees      = employees,
        search         = request.args.get("search", ""),
        last_refreshed = last_ref,
    )


# ========== CAPACITY EXPORTS ==========

@app.route('/capacity/plan/export')
def capacity_plan_export():
    auth = _require_auth()
    if auth: return auth
    if not ce:
        return "Export module not available", 500
    doc, _ = _get_cap_doc()
    plan, all_months = {}, []
    if doc:
        plan, all_months = _read_capacity_plan_sheet(doc)
    # Apply same LOB filter as plan view
    selected = request.args.getlist("lobs")
    if selected:
        plan = {k: v for k, v in plan.items() if k in selected}
    shrinkage = float(request.args.get("shrinkage", 30)) / 100
    data = ce.export_capacity_plan(plan, all_months)
    from flask import send_file
    import io
    buf = io.BytesIO(data)
    buf.seek(0)
    fname = f"capacity_plan_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/capacity/summary/export')
def capacity_summary_export():
    auth = _require_auth()
    if auth: return auth
    if not ce:
        return "Export module not available", 500
    doc, _ = _get_cap_doc()
    plan, all_months = {}, []
    if doc:
        plan, all_months = _read_capacity_plan_sheet(doc)
    filter_month  = request.args.get("month", "")
    filter_months = request.args.getlist("months")
    search        = request.args.get("search", "").strip().lower()
    selected_lobs = request.args.getlist("lobs")
    if filter_months:
        summary_rows = cp.build_capacity_summary(plan, all_months, filter_months=filter_months)
    else:
        summary_rows = cp.build_capacity_summary(plan, all_months, filter_month or None)
    if selected_lobs:
        summary_rows = [r for r in summary_rows if r["lob"] in selected_lobs]
    if search:
        summary_rows = [r for r in summary_rows if search in r["lob"].lower()]
    data = ce.export_capacity_summary(summary_rows)
    from flask import send_file
    import io
    buf = io.BytesIO(data)
    buf.seek(0)
    fname = f"capacity_summary_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/capacity/headcount/export')
def capacity_headcount_export():
    auth = _require_auth()
    if auth: return auth
    if not ce:
        return "Export module not available", 500
    emp_data, _ = _read_employees()
    hc_rows = cp.build_headcount_view(emp_data) if emp_data else []
    search = request.args.get("search", "").strip().lower()
    if search:
        hc_rows = [r for r in hc_rows if search in r["planning_unit"].lower()]
    data = ce.export_headcount(hc_rows)
    from flask import send_file
    import io
    buf = io.BytesIO(data)
    buf.seek(0)
    fname = f"headcount_{__import__('datetime').datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ========== RTM — Real Time Management ==========

try:
    import rtm as _rtm
    log.info("RTM module loaded OK")
except Exception as _rtm_err:
    log.error(f"RTM module failed to load: {_rtm_err}")
    import traceback
    log.error(traceback.format_exc())
    _rtm = None

# ========== People (LOA + Accommodations) ==========

try:
    from people_routes import register_people_routes
    register_people_routes(
        app,
        get_main_doc=lambda: main_doc,
        require_auth=_require_auth,
    )
    log.info("People routes registered OK")
except Exception as _people_err:
    log.error(f"People routes failed to register: {_people_err}")

@app.route('/rtm')
def rtm_index():
    auth = _require_auth()
    if auth: return auth
    return redirect('/rtm/dashboard')

@app.route('/rtm/dashboard')
def rtm_dashboard():
    auth = _require_auth()
    if auth: return auth

    today     = _rtm._today_est()
    now_str   = _rtm._now_est().strftime('%H:%M')
    today_str = today.isoformat()

    # ── Read agent status ONCE and share it across all sections ──
    # Previously read 3-4 times per load (cascades, under5, schedule, status detail).
    # Reading it once here and passing as override everywhere cuts 429 risk significantly.
    backup_agent_events = _get_agent_events_backup(today_str)
    if backup_agent_events:
        raw_agent_rows = backup_agent_events
    else:
        try:
            raw_agent_rows = _rtm._read_sheet(client, _rtm.AGENT_STATUS_ID) if _rtm else []
        except Exception as e:
            log.warning(f"Dashboard agent status read failed: {e}")
            raw_agent_rows = []

    backup_agent_rows = None
    if not backup_agent_events and _BACKUP_AGENT_DATE == today_str and _BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE):
        backup_agent_rows = _BACKUP_AGENT_ROWS[_BACKUP_AGENT_DATE]

    # ── Schedule & attendance ──
    try:
        schedule_rows = _rtm.get_schedule_data(today, gc=client,
                            override_agent_rows=backup_agent_rows,
                            override_raw_agent_rows=raw_agent_rows) if _rtm else []
        attendance      = _rtm.get_absenteeism_summary(schedule_rows) if _rtm else []
        absent_advisors = [{"employee": r["employee"], "lob": r["lob"]}
                           for r in schedule_rows
                           if r["shift_status"] == "Absent"
                           and str(r.get("personnel_number","") or "") not in _rtm.EXCLUDED_CURRENT_IDS
                           and str(r.get("current_id","") or "") not in _rtm.EXCLUDED_CURRENT_IDS]
    except Exception as e:
        log.warning(f"Dashboard schedule/attendance error: {e}")
        schedule_rows = []; attendance = []; absent_advisors = []

    # ── Interval report — aggregate by LOB ──
    req_date = request.args.get('backup_date', '')
    if req_date and req_date in _BACKUP_CALL_ROWS:
        interval_target_date = _date.fromisoformat(req_date)
        interval_call_rows = _BACKUP_CALL_ROWS[req_date]
    elif _BACKUP_CALL_DATE and _BACKUP_CALL_ROWS.get(_BACKUP_CALL_DATE):
        interval_target_date = _date.fromisoformat(_BACKUP_CALL_DATE)
        interval_call_rows = _BACKUP_CALL_ROWS[_BACKUP_CALL_DATE]
    else:
        interval_target_date = today
        interval_call_rows = None

    try:
        interval_rows = _rtm.get_interval_report(
            interval_target_date, gc=client,
            override_call_rows=interval_call_rows) if _rtm else []
    except Exception as e:
        log.warning(f"Dashboard interval report error: {e}")
        interval_rows = []
    interval_backup_active = interval_call_rows is not None
    interval_display_date = interval_target_date.isoformat()

    lob_agg = {}
    for r in interval_rows:
        if not r["in_biz"]:
            continue
        lob = r["lob"]
        if lob not in lob_agg:
            lob_agg[lob] = {"calls_offered": 0, "answered_within": 0,
                             "calls_abandoned": 0, "fc_offered": 0}
        lob_agg[lob]["calls_offered"]   += r["calls_offered"]
        lob_agg[lob]["answered_within"] += r["answered_within"]
        lob_agg[lob]["calls_abandoned"] += r["calls_abandoned"]
        lob_agg[lob]["fc_offered"]      += r["forecasted_cv"]

    # ── Cascades ──
    try:
        cascades = _rtm.get_cascades(today, gc=client,
                       override_raw_rows=raw_agent_rows) if _rtm else []
    except Exception as e:
        log.warning(f"Dashboard cascades error: {e}")
        cascades = []
    # Count all cascades — Power BI counts all day, not just business hours
    # calls_offered per agent comes from On-call + No-Answer events in agent status (now on each cascade row)
    cascade_agent = {}
    for c in cascades:
        key = (c["name"], c["lob"])
        if key not in cascade_agent:
            cascade_agent[key] = {
                "name":          c["name"],
                "lob":           c["lob"],
                "count":         0,
                "calls_offered": c.get("calls_offered", 0),  # from agent status events
            }
        cascade_agent[key]["count"] += 1
        # Keep the max calls_offered seen (all rows for same agent have same value)
        cascade_agent[key]["calls_offered"] = max(
            cascade_agent[key]["calls_offered"], c.get("calls_offered", 0)
        )

    cascade_by_advisor = sorted(cascade_agent.values(), key=lambda x: -x["count"])

    # ── Build interval_by_lob with cascade rate (all cascades) ──
    cascade_by_lob = {}
    for c in cascades:
        cascade_by_lob[c["lob"]] = cascade_by_lob.get(c["lob"], 0) + 1

    interval_by_lob = []
    for lob, d in sorted(lob_agg.items()):
        offered  = d["calls_offered"]
        aw       = d["answered_within"]
        aband    = d["calls_abandoned"]
        fc       = d["fc_offered"]
        # Only show LOBs that have actual calls today
        if offered == 0:
            continue
        casc_cnt = cascade_by_lob.get(lob, 0)
        sl       = round(aw / offered * 100, 1) if offered > 0 else None
        otf      = round(offered / fc * 100, 1) if fc > 0 else None
        aband_pct= round(aband / offered * 100, 1) if offered > 0 else None
        casc_rate= round(casc_cnt / offered * 100, 1) if offered > 0 else None
        interval_by_lob.append({
            "lob":           lob,
            "calls_offered": offered,
            "answered_within": aw,
            "calls_abandoned": aband,
            "service_level": sl,
            "otf":           otf,
            "abandoned_pct": aband_pct,
            "cascade_rate":  casc_rate,
        })

    # ── Totals ──
    total_offered = sum(d["calls_offered"]   for d in lob_agg.values())
    total_aw      = sum(d["answered_within"] for d in lob_agg.values())
    total_aband   = sum(d["calls_abandoned"] for d in lob_agg.values())
    total_fc      = sum(d["fc_offered"]      for d in lob_agg.values())
    total_casc    = sum(cascade_by_lob.values())
    total_sl      = round(total_aw / total_offered * 100, 1) if total_offered > 0 else None
    total_otf     = round(total_offered / total_fc * 100, 1) if total_fc > 0 else None
    total_cascade_rate = round(total_casc / total_offered * 100, 1) if total_offered > 0 else None

    # ── Status under 5 sec (Offline flashes) ──
    try:
        from collections import defaultdict
        agents_detail = _rtm.get_agent_status_detail(gc=client, override_raw_rows=raw_agent_rows) if _rtm else []
        # Use the already-read raw agent rows — no extra sheet read needed
        today_str2 = today.strftime("%Y-%m-%d")
        # Build per-user event lists
        by_uid = defaultdict(list)
        for r in raw_agent_rows:
            uid = str(r.get("user_id", "") or "").strip()
            st  = str(r.get("start_time", "") or "").strip()
            sid = str(r.get("c_activity_sid", "") or "").strip()
            if not st.startswith(today_str2):
                continue
            dt_cst = _rtm._parse_datetime_str(st)
            if not dt_cst:
                continue
            dt_est = _rtm._cp_to_est(dt_cst)
            status = _rtm.CP_STATUS_MAP.get(sid, "Unknown")
            by_uid[uid].append({"dt": dt_est, "status": status})

        # Build roster map current_id -> name/lob
        pm, um, _tm = _rtm._load_roster(client)
        uid_info = {}
        for v in pm.values():
            cid = v.get("current_id", "")
            if cid:
                uid_info[cid] = {"name": v.get("name", ""), "lob": v.get("lob", "")}

        under5_counts = defaultdict(lambda: {"name": "", "lob": "", "status": "", "count": 0})
        # Statuses that are normal even if brief — don't flag these
        UNDER5_EXCLUDE = {"Ready", "On-call", "Cool-Down"}
        for uid, events in by_uid.items():
            if uid in _rtm.EXCLUDED_CURRENT_IDS:
                continue
            events.sort(key=lambda x: x["dt"])
            info = uid_info.get(uid, {"name": f"UID {uid}", "lob": "Unknown"})
            for i, ev in enumerate(events):
                if ev["status"] in UNDER5_EXCLUDE:
                    continue
                if i + 1 >= len(events):
                    continue
                dur = (events[i+1]["dt"] - ev["dt"]).total_seconds()
                if dur < 5:
                    key = (uid, ev["status"])
                    under5_counts[key]["name"]   = info["name"]
                    under5_counts[key]["lob"]    = info["lob"]
                    under5_counts[key]["status"] = ev["status"]
                    under5_counts[key]["count"] += 1

        under5 = sorted(under5_counts.values(), key=lambda x: -x["count"])
    except Exception as e:
        log.warning(f"Under5 calc error: {e}")
        under5 = []

    # ── Build Teams copy text ──
    lines = []
    lines.append(f"📊 RTM Update — {today_str} {now_str} EST")
    lines.append("")

    lines.append("── INTERVAL REPORT ──")
    lines.append(f"{'LOB':<30} {'SVL':>7} {'OTF':>7} {'Casc%':>7} {'ABND%':>7}")
    lines.append("-" * 58)
    for r in interval_by_lob:
        sl_s   = f"{r['service_level']}%" if r['service_level'] is not None else "—"
        otf_s  = f"{r['otf']}%"           if r['otf']           is not None else "—"
        cr_s   = f"{r['cascade_rate']}%"  if r['cascade_rate']  is not None else "—"
        ab_s   = f"{r['abandoned_pct']}%" if r['abandoned_pct'] is not None else "—"
        lines.append(f"{r['lob']:<30} {sl_s:>7} {otf_s:>7} {cr_s:>7} {ab_s:>7}")
    tsl = f"{total_sl}%"   if total_sl   is not None else "—"
    totf= f"{total_otf}%"  if total_otf  is not None else "—"
    tcr = f"{total_cascade_rate}%" if total_cascade_rate is not None else "—"
    tabp= f"{round(total_aband/total_offered*100,1)}%" if total_offered > 0 else "—"
    lines.append(f"{'Total':<30} {tsl:>7} {totf:>7} {tcr:>7} {tabp:>7}")
    lines.append("")

    lines.append("── ATTENDANCE ──")
    lines.append(f"{'LOB':<30} {'Sched':>6} {'Absent':>7} {'Abs%':>6}")
    lines.append("-" * 52)
    for r in attendance:
        pct = round(r['total_absent'] / r['total_scheduled'] * 100) if r['total_scheduled'] > 0 else 0
        lines.append(f"{r['lob']:<30} {r['total_scheduled']:>6} {r['total_absent']:>7} {pct:>5}%")
    ts = sum(r['total_scheduled'] for r in attendance)
    ta = sum(r['total_absent']    for r in attendance)
    tp = round(ta / ts * 100) if ts > 0 else 0
    lines.append(f"{'Total':<30} {ts:>6} {ta:>7} {tp:>5}%")
    lines.append("")

    if absent_advisors:
        lines.append("── ABSENT ADVISORS ──")
        for r in absent_advisors:
            lines.append(f"  • {r['employee']} ({r['lob']})")
        lines.append("")

    if cascade_by_advisor:
        lines.append("── CASCADES BY ADVISOR ──")
        lines.append(f"{'Name':<28} {'LOB':<22} {'Casc':>5}")
        lines.append("-" * 58)
        for r in cascade_by_advisor:
            lines.append(f"{r['name']:<28} {r['lob']:<22} {r['count']:>5}")
        lines.append(f"{'Total':<50} {sum(r['count'] for r in cascade_by_advisor):>5}")
        lines.append("")

    if under5:
        lines.append("── STATUS UNDER 5 SEC ──")
        lines.append(f"{'Name':<28} {'LOB':<22} {'Count':>6}")
        lines.append("-" * 58)
        for r in under5:
            lines.append(f"{r['name']:<28} {r['lob']:<22} {r['count']:>6}")
        lines.append(f"{'Total':<50} {sum(r['count'] for r in under5):>6}")

    copy_text = "\n".join(lines)

    # ── AHOD (All Hands on Deck) — SS Sales contingency plan ──
    ss_sales_svl = None
    for r in interval_by_lob:
        if r['lob'].strip().lower() == AHOD_LOB.lower():
            ss_sales_svl = r['service_level']
            break

    if ss_sales_svl is None:
        ahod_tier = None
    elif ss_sales_svl >= 80:
        ahod_tier = None          # Green — healthy, no banner
    elif ss_sales_svl >= 75:
        ahod_tier = 'yellow'
    else:
        ahod_tier = 'red'

    ahod_levels = []
    if ahod_tier:
        today_acks = _AHOD_ACKS.get(today_str, {})
        # If memory was cleared (worker restart), rebuild today's acks from the
        # append-only ChecklistState log so the display stays accurate.
        if not today_acks and checklist_sheet is not None:
            try:
                rows = checklist_sheet.get_all_values()
                rebuilt = {}
                for r in rows[1:]:
                    if len(r) >= 7 and (r[0] or "").strip() == today_str and (r[1] or "").strip() == "ahod":
                        item = (r[2] or "").strip()   # "level_4"
                        if item.startswith("level_"):
                            try: lvl_n = int(item.split("_", 1)[1])
                            except Exception: continue
                            rebuilt[lvl_n] = {"checked": (r[4] == "1"), "by": r[5], "at": r[6]}
                if rebuilt:
                    _AHOD_ACKS[today_str] = rebuilt
                    today_acks = rebuilt
                del rows; gc.collect()
            except Exception:
                log.exception("AHOD ack rehydrate failed")
        visible_tiers = ['yellow'] if ahod_tier == 'yellow' else ['yellow', 'red']
        for lvl in AHOD_LEVELS:
            if lvl['tier'] not in visible_tiers:
                continue
            ack = today_acks.get(lvl['level'], {})
            ahod_levels.append({
                **lvl,
                "checked": ack.get("checked", False),
                "checked_by": ack.get("by", ""),
                "checked_at": ack.get("at", ""),
            })

    return render_template('rtm_dashboard.html',
        today              = today_str,
        now                = now_str,
        interval_by_lob    = interval_by_lob,
        interval_backup_active = interval_backup_active,
        interval_display_date  = interval_display_date,
        interval_backup_dates  = sorted(_BACKUP_CALL_ROWS.keys()) if _BACKUP_CALL_ROWS else [],
        attendance         = attendance,
        absent_advisors    = absent_advisors,
        cascade_by_advisor = cascade_by_advisor,
        under5             = under5,
        total_offered      = total_offered,
        total_aw           = total_aw,
        total_aband        = total_aband,
        total_sl           = total_sl,
        total_otf          = total_otf,
        total_cascade_rate = total_cascade_rate,
        copy_text          = copy_text,
        ahod_tier          = ahod_tier,
        ahod_svl           = ss_sales_svl,
        ahod_levels        = ahod_levels,
    )

@app.route('/rtm/schedule')
def rtm_schedule():
    auth = _require_auth()
    if auth: return auth
    target = request.args.get('date', _rtm._today_est().isoformat())
    try:
        from datetime import datetime
        target_date = datetime.strptime(target, '%Y-%m-%d').date()
    except Exception:
        from datetime import date
        target_date = date.today()

    rows = _rtm.get_schedule_data(target_date, gc=client,
               override_agent_rows=_BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE)
               if _BACKUP_AGENT_DATE == target_date.isoformat() and _BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE) else None,
               override_raw_agent_rows=_get_agent_events_backup(target_date.isoformat())) if _rtm else []
    lobs = sorted(set(r['lob'] for r in rows if r['lob']))
    employees = sorted(set(r['employee'] for r in rows if r['employee']))

    selected_lobs = request.args.getlist('lob')
    if selected_lobs:
        rows = [r for r in rows if r['lob'] in selected_lobs]

    selected_employees = request.args.getlist('employee')
    if selected_employees:
        rows = [r for r in rows if r['employee'] in selected_employees]

    status_filters = request.args.getlist('status')
    if status_filters:
        rows = [r for r in rows if r['shift_status'] in status_filters]

    return render_template('rtm_schedule.html',
        rows=rows,
        lobs=lobs,
        employees=employees,
        selected_lobs=selected_lobs,
        selected_employees=selected_employees,
        status_filters=status_filters,
        target_date=target_date.isoformat(),
        total=len(rows),
        present=sum(1 for r in rows if r['shift_status'] == 'Present'),
        absent=sum(1 for r in rows if r['shift_status'] == 'Absent'),
        not_started=sum(1 for r in rows if 'Not Yet Started' in r['shift_status']),
    )


@app.route('/network/ms-test')
def network_ms_test():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403

    import socket, time
    out = ["<h2>Microsoft Network Diagnostic</h2>",
           "<p>Testing outbound connectivity from this Render instance.</p><ul>"]

    targets = [
        ("login.microsoftonline.com", 443, "OAuth token endpoint"),
        ("graph.microsoft.com",       443, "Graph API endpoint"),
        ("accesself.sharepoint.com",  443, "SharePoint REST endpoint"),
        ("smtp.gmail.com",            587, "SMTP (known reference point)"),
        ("api.sendgrid.com",          443, "SendGrid API (known working)"),
    ]

    for host, port, label in targets:
        start = time.time()
        try:
            s = socket.create_connection((host, port), timeout=6)
            s.close()
            elapsed = round((time.time() - start) * 1000)
            out.append(f"<li>✅ <b>{host}:{port}</b> ({label}) — REACHABLE ({elapsed}ms)</li>")
        except Exception as e:
            elapsed = round((time.time() - start) * 1000)
            out.append(f"<li>❌ <b>{host}:{port}</b> ({label}) — FAILED: {e} ({elapsed}ms)</li>")

    out.append("</ul>")

    # Also try an actual HTTPS GET to graph.microsoft.com (unauthenticated —
    # should get a 401, which still proves the connection + TLS handshake works)
    try:
        import urllib.request
        req = urllib.request.Request("https://graph.microsoft.com/v1.0/", method="GET")
        try:
            urllib.request.urlopen(req, timeout=8)
        except urllib.error.HTTPError as he:
            out.append(f"<p>✅ HTTPS GET to graph.microsoft.com/v1.0/ got HTTP {he.code} "
                       f"(expected 401 Unauthorized — this means the connection itself works)</p>")
        except Exception as e2:
            out.append(f"<p>❌ HTTPS GET to graph.microsoft.com/v1.0/ failed: {e2}</p>")
    except Exception as e:
        out.append(f"<p>❌ urllib error: {e}</p>")

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
            method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=8)
            out.append(f"<p>✅ HTTPS GET to login.microsoftonline.com discovery doc — "
                       f"HTTP {resp.status} (OAuth endpoint IS reachable)</p>")
        except Exception as e2:
            out.append(f"<p>❌ HTTPS GET to login.microsoftonline.com discovery doc failed: {e2}</p>")
    except Exception as e:
        out.append(f"<p>❌ urllib error: {e}</p>")

    return "".join(out)


def rtm_debug():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403
    from datetime import date
    import traceback
    out = ["<h2>RTM Debug</h2>"]
    out.append(f"<p>Today: {date.today()} | target_str: {date.today().strftime('%Y/%m/%d')}</p>")
    out.append(f"<p>_rtm loaded: {_rtm is not None}</p>")
    try:
        import rtm as _rtm_test
        out.append("<p style='color:green'>✅ rtm import test OK</p>")
    except Exception as ie:
        out.append(f"<p style='color:red'>❌ rtm import error: {ie}</p>")
        import traceback as tb
        out.append(f"<pre>{tb.format_exc()}</pre>")
    out.append(f"<p>main client: {client is not None}</p>")
    out.append(f"<p>SERVICE_ACCOUNT_FILE var: {SERVICE_ACCOUNT_FILE}</p>")
    out.append(f"<p>File exists: {__import__('os').path.exists(SERVICE_ACCOUNT_FILE)}</p>")

    if not _rtm:
        out.append("<p style='color:red'>❌ rtm module not loaded</p>")
        return "<br>".join(out)

    # Use existing portal client instead of creating new one
    try:
        gc = client  # reuse already-authorized client
        if gc is None:
            raise Exception("Main gspread client is None")
        out.append("<p style='color:green'>✅ Using existing gspread client</p>")
    except Exception as e:
        out.append(f"<p style='color:red'>❌ client error: {e}</p>")
        try:
            gc = _rtm._get_client()
            out.append("<p style='color:orange'>⚠ Fell back to _get_client()</p>")
        except Exception as e2:
            out.append(f"<p style='color:red'>❌ _get_client also failed: {e2}</p>")
            return "<br>".join(out)

    # Test schedule sheet
    try:
        doc  = gc.open_by_key(_rtm.SCHEDULE_SHEET_ID)
        tabs = [ws.title for ws in doc.worksheets()]
        out.append(f"<p style='color:green'>✅ Schedule sheet opened — tabs: {tabs}</p>")
        try:
            ws   = doc.worksheet("Schedule")
            rows = ws.get_all_records()
            out.append(f"<p style='color:green'>✅ Schedule tab — {len(rows)} rows</p>")
            if rows:
                out.append(f"<p>Headers: {list(rows[0].keys())}</p>")
                out.append(f"<p>Row 1 date: '{rows[0].get('Date', 'N/A')}'</p>")
                today_rows = [r for r in rows if str(r.get('Date','')).strip() == date.today().strftime('%Y/%m/%d')]
                out.append(f"<p>Rows matching today ({date.today().strftime('%Y/%m/%d')}): {len(today_rows)}</p>")
                if rows:
                    out.append(f"<p>Sample dates in sheet: {list(set(str(r.get('Date','')) for r in rows[:20]))}</p>")
        except Exception as e:
            out.append(f"<p style='color:red'>❌ Schedule tab error: {e}</p>")
    except Exception as e:
        out.append(f"<p style='color:red'>❌ Schedule sheet FAILED: {e}</p>")

    # Test agent status sheet
    try:
        doc2 = gc.open_by_key(_rtm.AGENT_STATUS_ID)
        tabs2 = [ws.title for ws in doc2.worksheets()]
        out.append(f"<p style='color:green'>✅ Agent Status sheet opened — tabs: {tabs2}</p>")
        ws2  = doc2.get_worksheet(0)
        rows2 = ws2.get_all_records()
        out.append(f"<p style='color:green'>✅ Agent Status — {len(rows2)} rows</p>")
        if rows2:
            out.append(f"<p>Headers: {list(rows2[0].keys())}</p>")
            out.append(f"<p>Row 1: {rows2[0]}</p>")
    except Exception as e:
        out.append(f"<p style='color:red'>❌ Agent Status FAILED: {e}<br>{traceback.format_exc()}</p>")

    # Test roster sheet
    try:
        doc3 = gc.open_by_key(_rtm.ROSTER_SHEET_ID)
        ws3  = doc3.worksheet("EMPLOYEES")
        rows3 = ws3.get_all_records()
        out.append(f"<p style='color:green'>✅ Roster — {len(rows3)} rows</p>")
        if rows3:
            out.append(f"<p>Headers: {list(rows3[0].keys())}</p>")
    except Exception as e:
        out.append(f"<p style='color:red'>❌ Roster FAILED: {e}</p>")

    # Actually call get_schedule_data and show results
    try:
        from datetime import date as _date
        rows = _rtm.get_schedule_data(_date.today(), gc=gc)
        out.append(f"<p style='color:green'>✅ get_schedule_data returned {len(rows)} rows</p>")
        if rows:
            out.append(f"<p>Sample row: {rows[0]}</p>")

        # Check schedule blocks sheet for VTO
        out.append("<h3>Schedule Blocks Sample</h3>")
        try:
            blk_rows = _rtm._read_sheet(gc, _rtm.SCHEDULE_BLOCKS_ID)
            today_str2 = _date.today().strftime("%Y-%m-%d")
            today_blks = [r for r in blk_rows if str(r.get("Date","")).strip() == today_str2]
            out.append(f"<p>Blocks sheet: {len(blk_rows)} total, {len(today_blks)} today</p>")
            if today_blks:
                out.append(f"<p>Headers: {list(today_blks[0].keys())}</p>")
                out.append(f"<p>Row 1: {today_blks[0]}</p>")
                activities = set(r.get('Activity','') for r in today_blks)
                out.append(f"<p>Activities today: {sorted(activities)}</p>")
                vto = [r for r in today_blks if 'vto' in str(r.get('Activity','')).lower()]
                out.append(f"<p>VTO blocks: {len(vto)}</p>")
                if vto:
                    out.append(f"<p>VTO sample: {vto[0]}</p>")
        except Exception as e:
            out.append(f"<p style='color:red'>Blocks error: {e}</p>")

        # Diagnose the join
        out.append("<h3>Join Diagnostics</h3>")
        sched_rows = _rtm._read_sheet(gc, _rtm.SCHEDULE_SHEET_ID, tab="Schedule")
        today_str  = _date.today().strftime("%Y/%m/%d")
        today_sched = [r for r in sched_rows if str(r.get("Date","")).strip() == today_str]
        pm, um, _tm = _rtm._load_roster(gc)
        ag = _rtm._load_agent_status(gc)

        out.append(f"<p>Roster personnel_map sample keys: {list(pm.keys())[:5]}</p>")
        out.append(f"<p>Roster userid_map sample keys: {list(um.keys())[:5]}</p>")
        out.append(f"<p>Agent status keys (user_ids): {list(ag.keys())[:5]}</p>")

        if today_sched:
            sample = today_sched[0]
            pers = str(sample.get("Personnel Number","")).strip()
            out.append(f"<p>Sample schedule personnel#: '{pers}'</p>")
            roster_hit = pm.get(pers, {})
            out.append(f"<p>Roster lookup result: {roster_hit}</p>")
            emp_id = roster_hit.get("employee_id","")
            out.append(f"<p>employee_id from roster: '{emp_id}'</p>")
            ag_hit = ag.get(str(emp_id), {})
            out.append(f"<p>Agent status lookup result: {ag_hit}</p>")
            out.append(f"<p>Agent status key type sample: {type(list(ag.keys())[0]) if ag else 'empty'}</p>")
        # Show current_id vs agent status keys comparison
        out.append("<h3>ID Mapping Check</h3>")
        pm2, nm2, _tm2 = _rtm._load_roster(gc)
        sample_ids = [(v['name'], v.get('current_id',''), v.get('employee_id',''))
                      for v in list(pm2.values())[:10]]
        out.append(f"<p>Roster sample (name, current_id, employee_id):</p>")
        for name, cid, eid in sample_ids:
            in_ag = cid in ag
            out.append(f"<p>&nbsp;&nbsp;{name} | current_id={cid} | emp_id={eid} | in_agent_status={in_ag}</p>")
        out.append(f"<p>Agent status sample user_ids: {list(ag.keys())[:10]}</p>")
        # Check if any current_ids match agent status keys
        matches = [cid for _,cid,_ in sample_ids if cid in ag]
        out.append(f"<p>Matches found in sample: {matches}</p>")
        # Also force-refresh agent status cache
        _rtm._rtm_cache.clear()
        out.append("<p style='color:orange'>⚠ Cache cleared — reload page to see fresh data</p>")
        # Show raw agent status times for the matched employee
        if ag_hit:
            out.append(f"<p>Raw agent status times: {ag_hit}</p>")
        # Show a few raw agent status rows to check timezone
        # Force fresh read of agent status bypassing cache
        try:
            doc_ag = gc.open_by_key(_rtm.AGENT_STATUS_ID)
            ws_ag  = doc_ag.get_worksheet(0)
            raw_ag = ws_ag.get_all_records()
            all_ag_dates = set(str(r.get('start_time',''))[:10] for r in raw_ag if r.get('start_time'))
            out.append(f"<p>Agent status ALL dates in sheet (fresh read): {sorted(all_ag_dates)}</p>")
            today_ag = [r for r in raw_ag if str(r.get('start_time','')).startswith(_date.today().strftime('%Y-%m-%d'))]
            out.append(f"<p>Agent rows for today ({_date.today()}): {len(today_ag)}</p>")
            if today_ag:
                out.append(f"<p>Sample times: {[r['start_time'] for r in today_ag[:5]]}</p>")
        except Exception as e:
            out.append(f"<p style='color:red'>Agent fresh read error: {e}</p>")
        else:
            out.append("<p style='color:orange'>⚠ 0 rows — checking internals...</p>")
            sched_rows = _rtm._read_sheet(gc, _rtm.SCHEDULE_SHEET_ID, tab="Schedule")
            today_str = _date.today().strftime("%Y/%m/%d")
            matching = [r for r in sched_rows if str(r.get("Date","")).strip() == today_str]
            out.append(f"<p>Direct sheet: {len(sched_rows)} total, {len(matching)} match {today_str}</p>")
            if matching:
                out.append(f"<p>Sample: {matching[0]}</p>")
                pm, um, _tm = _rtm._load_roster(gc)
                out.append(f"<p>Roster: {len(pm)} personnel, {len(um)} user_ids</p>")
                pers = str(matching[0].get("Personnel Number","")).strip()
                out.append(f"<p>First personnel#: '{pers}' in roster: {pers in pm}</p>")
                if pm:
                    out.append(f"<p>Sample roster keys: {list(pm.keys())[:5]}</p>")
    except Exception as e:
        import traceback as tb
        out.append(f"<p style='color:red'>❌ get_schedule_data error: {e}</p>")
        out.append(f"<pre>{tb.format_exc()}</pre>")

    return "<br>".join(out)

@app.route('/rtm/absenteeism')
def rtm_absenteeism():
    auth = _require_auth()
    if auth: return auth
    today_str = _rtm._today_est().strftime("%Y-%m-%d") if _rtm else ""
    backup_agent_rows = _BACKUP_AGENT_ROWS.get(_BACKUP_AGENT_DATE) if _BACKUP_AGENT_DATE == today_str else None
    schedule_rows = _rtm.get_schedule_data(_rtm._today_est(), gc=client,
                        override_agent_rows=backup_agent_rows,
                        override_raw_agent_rows=_get_agent_events_backup(today_str)) if _rtm else []

    all_lobs      = sorted(set(r['lob'] for r in schedule_rows if r['lob']))
    all_employees = sorted(set(r['employee'] for r in schedule_rows if r['employee']))
    selected_lobs      = request.args.getlist('lob')
    selected_employees = request.args.getlist('employee')
    if selected_lobs:
        schedule_rows = [r for r in schedule_rows if r['lob'] in selected_lobs]
    if selected_employees:
        schedule_rows = [r for r in schedule_rows if r['employee'] in selected_employees]

    summary       = _rtm.get_absenteeism_summary(schedule_rows) if _rtm else []
    # Strip "Left Early" from any row where the agent has since logged back in.
    # Cached rows may have "Left Early" set from a brief disconnect — if the agent's
    # current status is not Offline, they returned and should not be flagged.
    for r in schedule_rows:
        if 'left early' in r.get('shift_timing_status', '').lower():
            recent = r.get('recent_status', '')
            if recent and recent != 'Offline':
                # Agent is back online — remove the Left Early flag
                ts = r['shift_timing_status']
                ts = ', '.join(part.strip() for part in ts.split(',')
                               if 'left early' not in part.lower())
                r['shift_timing_status'] = ts

    absent_rows = [r for r in schedule_rows if r['shift_status'] == 'Absent']
    late_rows   = [r for r in schedule_rows
                   if any(x in r.get('shift_timing_status','').lower()
                          for x in ['started late','late arrival','delayed start'])
                   and r['shift_status'] == 'Present']
    early_rows  = [r for r in schedule_rows
                   if 'left early' in r.get('shift_timing_status','').lower()
                   and r.get('vto','') != 'Yes']
    vto_rows    = [r for r in schedule_rows if r.get('schedule_note')]

    total_sched  = sum(s['total_scheduled']   for s in summary)
    total_absent = sum(s['total_absent']      for s in summary)
    total_late   = sum(s['total_late']        for s in summary)
    total_early  = sum(s['total_early_leave'] for s in summary)

    return render_template('rtm_absenteeism.html',
        summary=summary,
        absent_rows=absent_rows,
        late_rows=late_rows,
        early_rows=early_rows,
        vto_rows=vto_rows,
        total_sched=total_sched,
        total_absent=total_absent,
        total_late=total_late,
        total_early=total_early,
        all_lobs=all_lobs,
        all_employees=all_employees,
        selected_lobs=selected_lobs,
        selected_employees=selected_employees,
        today=_rtm._today_est().isoformat(),
    )


@app.route('/rtm/pw-schedules')
def rtm_pw_schedules():
    auth = _require_auth()
    if auth: return auth
    from datetime import date
    rows = _rtm.get_pw_schedules(_rtm._today_est(), gc=client) if _rtm else []
    employees = sorted(set(r['employee'] for r in rows if r['employee']))
    activities = sorted(set(r['activity'] for r in rows if r['activity']))
    lobs = sorted(set(r['lob'] for r in rows if r['lob']))

    selected_employees = request.args.getlist('employee')
    selected_activities = request.args.getlist('activity')
    selected_lobs = request.args.getlist('lob')

    if selected_employees:
        rows = [r for r in rows if r['employee'] in selected_employees]
    if selected_activities:
        rows = [r for r in rows if r['activity'] in selected_activities]
    if selected_lobs:
        rows = [r for r in rows if r['lob'] in selected_lobs]

    return render_template('rtm_pw_schedules.html',
        rows=rows, employees=employees, activities=activities, lobs=lobs,
        selected_employees=selected_employees,
        selected_activities=selected_activities,
        selected_lobs=selected_lobs,
        today=_rtm._today_est().isoformat(),
        total_blocks=len(rows),
    )

@app.route('/rtm/forecast')
def rtm_forecast():
    auth = _require_auth()
    if auth: return auth
    lob_summary, detail_rows = (_rtm.get_forecast_data(_rtm._today_est(), gc=client)
                                if _rtm else ([], []))
    call_summary = (_rtm.get_call_status_summary(_rtm._today_est(), gc=client)
                    if _rtm else [])
    # Aggregate call status by base LOB (combining EN+FR) to match forecast rows
    # Forecast: "SS Sales" = Combined (SS Sales EN + FR + Combined workloads summed)
    # Call status: sum "SS Sales EN" + "SS Sales FR" → "SS Sales"
    call_by_lob = {}
    for r in call_summary:
        lob = r['lob']  # base LOB without language
        if lob not in call_by_lob:
            call_by_lob[lob] = {'calls_offered': 0, 'answered_within': 0,
                                 'abandoned': 0, 'rolled_over': 0}
        call_by_lob[lob]['calls_offered']   += r['calls_offered']
        call_by_lob[lob]['answered_within'] += r['answered_within']
        call_by_lob[lob]['abandoned']       += r['abandoned']
    for lob, d in call_by_lob.items():
        o = d['calls_offered']
        d['service_level'] = round(d['answered_within'] / o * 100, 1) if o else 0
        d['abandoned_pct'] = round(d['abandoned'] / o * 100, 1) if o else 0

    # Build combined table — forecast drives the rows, call status joined by base LOB
    combined = []
    for fc in lob_summary:
        lob       = fc['lob']
        cv        = call_by_lob.get(lob, {})
        fc_utonow = fc.get('utonow_cv', 0)
        offered   = cv.get('calls_offered', 0)
        otf       = round(offered / fc_utonow * 100, 1) if fc_utonow > 0 and offered > 0 else None
        combined.append({
            'lob':             lob,
            'daily_cv':        fc.get('daily_cv', 0),
            'utonow_cv':       fc_utonow,
            'progress_pct':    fc.get('progress_pct', 0),
            'calls_offered':   offered,
            'answered_within': cv.get('answered_within', 0),
            'service_level':   cv.get('service_level', None) if offered else None,
            'abandoned_pct':   cv.get('abandoned_pct', None) if offered else None,
            'otf':             otf,
        })

    # EN/FR detail breakdown — add OTF using combined forecast utonow
    fc_utonow_by_lob = {r['lob']: r['utonow_cv'] for r in lob_summary}
    call_detail = []
    for r in sorted(call_summary, key=lambda x: (x['lob'], x['lang'])):
        fc_utonow = fc_utonow_by_lob.get(r['lob'], 0)
        offered   = r['calls_offered']
        # OTF per language = offered / (combined forecast utonow)
        # Note: this shows each language vs the total combined forecast
        otf = round(offered / fc_utonow * 100, 1) if fc_utonow > 0 and offered > 0 else None
        call_detail.append({**r, 'otf': otf})

    total_daily   = sum(r['daily_cv']      for r in lob_summary)
    total_utonow  = sum(r['utonow_cv']     for r in lob_summary)
    total_offered = sum(r['calls_offered'] for r in call_summary)
    total_prog    = round(total_utonow / total_daily * 100, 1) if total_daily > 0 else 0
    total_otf     = round(total_offered / total_utonow * 100, 1) if total_utonow > 0 else None

    all_lobs = sorted(set(r['lob'] for r in combined if r['lob']))
    selected_lobs = request.args.getlist('lob')
    if selected_lobs:
        combined    = [r for r in combined    if r['lob'] in selected_lobs]
        call_detail = [r for r in call_detail if r['lob'] in selected_lobs]

    return render_template('rtm_forecast.html',
        combined=combined,
        detail_rows=detail_rows,
        call_detail=call_detail,
        total_daily=total_daily,
        total_utonow=total_utonow,
        total_offered=total_offered,
        total_prog=total_prog,
        total_otf=total_otf,
        all_lobs=all_lobs,
        selected_lobs=selected_lobs,
        today=_rtm._today_est().isoformat(),
        now=_rtm._now_est().strftime('%H:%M'),
    )

@app.route('/rtm/knowledge')
def rtm_knowledge():
    auth = _require_auth()
    if auth: return auth
    from datetime import date
    rows = _rtm.get_ka_data(gc=client) if _rtm else []
    search = request.args.get('search', '').strip().lower()
    if search:
        rows = [r for r in rows if search in r['name'].lower()]
    today_rows = [r for r in rows
                  if r['complete_time'].startswith(date.today().isoformat())]
    return render_template('rtm_knowledge.html',
        rows=rows,
        today_rows=today_rows,
        search=search,
        total=len(rows),
        today_count=len(today_rows),
        today=_rtm._today_est().isoformat(),
    )


@app.route('/rtm/ahod/ack', methods=['POST'])
def rtm_ahod_ack():
    auth = _require_auth()
    if auth: return auth
    try:
        level   = int(request.form.get('level', 0))
        checked = request.form.get('checked', '0') == '1'
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid input"}), 400

    today_str = _rtm._today_est().isoformat() if _rtm else ''
    who   = session.get('user_email', 'unknown')
    stamp = _rtm._now_est().strftime('%H:%M') if _rtm else ''
    _AHOD_ACKS.setdefault(today_str, {})
    _AHOD_ACKS[today_str][level] = {"checked": checked, "by": who, "at": stamp}

    # Also log to ChecklistState as an append-only audit trail (same as the
    # daily checklist), so who-checked-what survives worker restarts and the
    # full history is kept. Non-fatal if the sheet is unavailable.
    try:
        if checklist_sheet is not None:
            # cols: date, kind, item_id, task, value, who, updated_at
            safe_append_row(checklist_sheet,
                            [today_str, "ahod", "level_" + str(level),
                             "AHOD Level " + str(level),
                             "1" if checked else "0", who, stamp])
    except Exception:
        log.exception("AHOD ack sheet log failed")
    return jsonify({"ok": True, "by": who, "at": stamp})


@app.route('/rtm/cache-clear')
def rtm_cache_clear():
    auth = _require_auth()
    if auth: return auth
    if _rtm:
        _rtm.expire_all_cache()
    return f"<h2>Cache refreshed</h2><p>All RTM data will be re-fetched on next load — old data kept as a safety net if any single sheet fails to refresh. <a href='/rtm/schedule'>Go to Schedule</a> | <a href='/rtm/debug'>Debug</a></p>"


@app.route('/rtm/forecast-debug')
def rtm_forecast_debug():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403
    from datetime import date
    out = ["<h2>Forecast Debug</h2>"]
    out.append(f"<p>Today EST: {_rtm._today_est()} | Now EST: {_rtm._now_est().strftime('%H:%M:%S')}</p>")

    # Raw forecast data
    gc = client
    fc_rows = _rtm._read_sheet(gc, _rtm.FORECAST_SHEET_ID, tab="Forecasts V1.0")
    today_str = _rtm._today_est().strftime("%Y-%m-%d")
    today_fc = [r for r in fc_rows
                if str(r.get("interval_start","")).startswith(today_str)
                and "operational" in str(r.get("forecast_type","")).lower()
                and "offered" in str(r.get("metric","")).lower()]
    out.append(f"<p>Forecast rows for today (operational+offered): {len(today_fc)}</p>")

    # Show unique workload_names
    wnames = sorted(set(r.get("workload_name","") for r in today_fc))
    out.append(f"<p>Workload names: {wnames}</p>")

    # Sum by workload_name
    by_wname = {}
    for r in today_fc:
        w = r.get("workload_name","")
        try: v = float(r.get("value",0) or 0)
        except: v = 0
        by_wname[w] = by_wname.get(w, 0) + v
    out.append("<h3>Daily CV by workload_name</h3>")
    for w, v in sorted(by_wname.items()):
        combined = _rtm.WORKLOAD_MAPPING.get(w, "UNMAPPED")
        in_combined = w in _rtm.get_forecast_data.__code__.co_consts
        out.append(f"<p>{w} → {combined} | daily={round(v)}</p>")

    # Call status
    cs_rows = _rtm._read_sheet(gc, _rtm.CALL_STATUS_ID)
    today_cs = [r for r in cs_rows]
    # Check dates in call status
    cs_dates = set(str(r.get("date_created",""))[:10] for r in cs_rows if r.get("date_created"))
    out.append(f"<h3>Call Status</h3>")
    out.append(f"<p>Total rows: {len(cs_rows)} | Dates: {sorted(cs_dates)[-3:]}</p>")

    # Show unique queue_ids and their LOB mapping
    from datetime import timedelta
    now_est = _rtm._now_est()
    mins = now_est.minute
    rounded_min = (mins // 15) * 15
    is_quarter = (mins % 15) == 0
    if is_quarter:
        adj_time = now_est.replace(minute=rounded_min, second=0, microsecond=0)
    else:
        adj_time = now_est - timedelta(minutes=30)
    out.append(f"<p>Adjusted time for call status: {adj_time.strftime('%H:%M:%S')}</p>")

    # Count by LOB
    lob_counts = {}
    unmapped = set()
    for r in cs_rows:
        dc = str(r.get("date_created","") or "").strip()
        dt_cst = _rtm._parse_datetime_str(dc)
        if not dt_cst: continue
        dt_est = _rtm._cp_to_est(dt_cst)
        if dt_est.date() != _rtm._today_est(): continue
        if dt_est > adj_time: continue
        try: qid = int(r.get("queue_id",0) or 0)
        except: qid = 0
        info = _rtm.QUEUE_MAP.get(qid)
        if info:
            lob = info[0]
            lob_counts[lob] = lob_counts.get(lob, 0) + 1
        else:
            unmapped.add(qid)

    out.append("<h3>Call counts by LOB (up to adjusted time)</h3>")
    for lob, cnt in sorted(lob_counts.items()):
        out.append(f"<p>{lob}: {cnt}</p>")
    out.append(f"<p>Unmapped queue_ids: {sorted(unmapped)[:20]}</p>")

    return "<br>".join(out)


@app.route('/rtm/interval')
def rtm_interval():
    auth = _require_auth()
    if auth: return auth
    selected_lobs = request.args.getlist('lob')
    today = _rtm._today_est() if _rtm else None
    today_str = today.strftime("%Y-%m-%d") if today else ""

    backup_active = False
    # Check for a backup date override (?backup_date=YYYY-MM-DD), falling
    # back to the most recent uploaded date, then to today's live data.
    req_date = request.args.get('backup_date', '')
    if req_date and req_date in _BACKUP_CALL_ROWS:
        use_date = req_date
    elif _BACKUP_CALL_DATE and _BACKUP_CALL_ROWS.get(_BACKUP_CALL_DATE):
        use_date = _BACKUP_CALL_DATE
    else:
        use_date = None

    if use_date and _BACKUP_CALL_ROWS.get(use_date) and _rtm:
        backup_rows = _BACKUP_CALL_ROWS[use_date]
        try:
            from datetime import date as _date
            backup_date = _date.fromisoformat(use_date)
        except Exception:
            backup_date = today
        all_rows = _rtm.get_interval_report(backup_date, gc=client,
                                             override_call_rows=backup_rows)
        backup_active = True
    else:
        all_rows = _rtm.get_interval_report(today, gc=client) if _rtm else []

    # Build picker options from the FULL unfiltered dataset so the list
    # never collapses down to just whatever is currently selected
    lobs = sorted(set(r['lob'] for r in all_rows))

    # Apply LOB filter for display only, after picker options are captured
    rows = [r for r in all_rows if r['lob'] in selected_lobs] if selected_lobs else all_rows

    total_fc      = sum(r['forecasted_cv']   for r in rows)
    total_offered = sum(r['calls_offered']   for r in rows)
    total_answered= sum(r['calls_answered']  for r in rows)
    total_aw      = sum(r['answered_within'] for r in rows)
    total_aband   = sum(r['calls_abandoned'] for r in rows)
    total_sl      = round(total_aw / total_offered * 100, 1) if total_offered else None
    total_otf     = round(total_offered / total_fc * 100, 1) if total_fc else None
    return render_template('rtm_interval.html',
        rows=rows,
        lobs=lobs,
        selected_lobs=selected_lobs,
        total_fc=total_fc,
        total_offered=total_offered,
        total_answered=total_answered,
        total_aw=total_aw,
        total_aband=total_aband,
        total_sl=total_sl,
        total_otf=total_otf,
        today=_rtm._today_est().isoformat() if _rtm else '',
        now=_rtm._now_est().strftime('%H:%M') if _rtm else '',
        backup_active=backup_active,
        backup_date=use_date or _BACKUP_CALL_DATE,
        backup_dates=sorted(_BACKUP_CALL_ROWS.keys()) if _BACKUP_CALL_ROWS else [],
    )

@app.route('/rtm/cs-debug')
def rtm_cs_debug():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403
    out = ["<h2>Call Status Raw Sheet Debug</h2>"]
    gc = client
    rows = _rtm._read_sheet(gc, _rtm.CALL_STATUS_ID)
    if not rows:
        return "No rows"
    out.append(f"<p>Total rows: {len(rows)}</p>")
    out.append(f"<p>Headers: {list(rows[0].keys())}</p>")
    
    # Show sample rows
    today_str = _rtm._today_est().strftime("%Y-%m-%d")
    today_rows = []
    for r in rows:
        dc = str(r.get("date_created","") or "").strip()
        if dc.startswith(today_str):
            today_rows.append(r)
    out.append(f"<p>Rows for today (raw CST): {len(today_rows)}</p>")
    if today_rows:
        out.append(f"<p>Sample row: {today_rows[0]}</p>")
        out.append(f"<p>Sample row 2: {today_rows[1] if len(today_rows)>1 else ''}</p>")
        
    # Check Calls Offered column values
    co_vals = set(str(r.get("Calls Offered","") or r.get("calls_offered","") or "") 
                  for r in today_rows[:50])
    out.append(f"<p>Calls Offered column sample values: {co_vals}</p>")
    
    # Check all column names exactly
    out.append(f"<p>All columns: {list(rows[0].keys())}</p>")
    
    # Check Answered Within column
    aw_vals = set(str(r.get("Answered Within","") or r.get("answered_within","") or "")
                  for r in today_rows[:50])
    out.append(f"<p>Answered Within column sample values: {aw_vals}</p>")
    
    # Check LOB column
    lob_vals = set(str(r.get("LOB","") or "") for r in today_rows[:100] if r.get("LOB",""))
    out.append(f"<p>LOB column values: {lob_vals}</p>")
    
    # Check Interval column  
    int_vals = sorted(set(str(r.get("Interval","") or "") for r in today_rows[:200] if r.get("Interval","")))[:20]
    out.append(f"<p>Interval column values (sample): {int_vals}</p>")

    return "<br>".join(out)

@app.route('/rtm/call-log')
def rtm_call_log():
    auth = _require_auth()
    if auth: return auth
    gc = client

    # Filters
    selected_lobs   = request.args.getlist('lob')
    interval_filter = request.args.get('interval', '').strip()
    lang_filter     = request.args.get('lang', '').strip()
    show_ooh        = request.args.get('ooh', '0') == '1'
    queue_filter    = request.args.get('queue_id', '').strip()

    today     = _rtm._today_est()
    today_str = today.strftime("%Y-%m-%d")

    rows_raw = _rtm._read_sheet(gc, _rtm.CALL_STATUS_ID)

    # Process rows
    calls = []
    seen  = set()
    for r in rows_raw:
        dc = str(r.get("date_created", "") or "").strip()
        dt_cst = _rtm._parse_datetime_str(dc)
        if not dt_cst:
            continue
        dt_est = _rtm._cp_to_est(dt_cst)
        if dt_est.date() != today:
            continue

        # Map queue to LOB
        try:
            qid = int(r.get("queue_id", 0) or 0)
        except:
            qid = 0
        qi = _rtm.QUEUE_MAP.get(qid)
        if not qi:
            continue
        lob, lang = qi

        in_biz    = _rtm._in_business_hours(dt_est, lob)
        log_id    = str(r.get("log_id", "") or "").strip()
        raw_aband = r.get("is_abandoned", "")
        raw_rolled= r.get("is_rolled_over", "")
        # is_abandoned=1   → caller hung up (ABND / lost call)
        # is_rolled_over=1 → call rolled to store/other location (not lost)
        is_aband  = 1 if str(raw_aband).strip()  in ("1", "1.0") else 0
        is_rolled = 1 if str(raw_rolled).strip() in ("1", "1.0") else 0
        try:
            qt = float(r.get("queue_time", 0) or 0)
        except:
            qt = 0
        try:
            dur = float(r.get("duration", 0) or 0)
        except:
            dur = 0

        # 30-min interval bucket
        bucket_min = (dt_est.minute // 30) * 30
        bucket     = dt_est.replace(minute=bucket_min, second=0, microsecond=0)
        interval   = bucket.strftime("%H:%M:%S")

        aw = 1 if (not is_aband and not is_rolled and qt <= 20) else 0

        call = {
            "date_created":  dt_est.strftime("%Y-%m-%d %H:%M:%S"),
            "log_id":        log_id,
            "queue_id":      qid,
            "lob":           lob,
            "language":      lang,
            "lob_language":  f"{lob} {lang}".strip(),
            "type":          str(r.get("Type", "") or "").strip(),
            "interval":      interval,
            "is_rolled_over":is_rolled,
            "is_abandoned":  is_aband,
            "queue_time":    int(qt) if qt else 0,
            "duration":      int(dur) if dur else 0,
            "answered_within": aw,
            "in_biz":        in_biz,
        }

        # Apply filters
        if selected_lobs and lob not in selected_lobs:
            continue
        if lang_filter and lang != lang_filter:
            continue
        if interval_filter and interval != interval_filter:
            continue
        if queue_filter and str(qid) != queue_filter:
            continue
        if not show_ooh and not in_biz:
            continue

        calls.append(call)

    # Sort by date_created desc
    calls.sort(key=lambda x: x["date_created"], reverse=True)

    # Build filter options from ALL today's calls (pre-filter)
    all_lobs = sorted(set(
        _rtm.QUEUE_MAP[int(r.get("queue_id",0))][0]
        for r in rows_raw
        if str(r.get("date_created","")).startswith(today_str)
        and int(r.get("queue_id",0) or 0) in _rtm.QUEUE_MAP
    ))
    all_queue_ids = sorted(set(
        int(r.get("queue_id",0) or 0)
        for r in rows_raw
        if str(r.get("date_created","")).startswith(today_str)
        and int(r.get("queue_id",0) or 0) in _rtm.QUEUE_MAP
    ))
    # Build display labels for queue dropdown: {qid: "123 — SS Sales EN"}
    queue_id_labels = {
        qid: f"{qid} — {_rtm.QUEUE_MAP[qid][0]} {_rtm.QUEUE_MAP[qid][1]}".strip()
        for qid in all_queue_ids if qid in _rtm.QUEUE_MAP
    }

    return render_template("rtm_call_log.html",
        calls=calls,
        all_lobs=all_lobs,
        all_queue_ids=all_queue_ids,
        queue_id_labels=queue_id_labels,
        selected_lobs=selected_lobs,
        interval_filter=interval_filter,
        lang_filter=lang_filter,
        queue_filter=queue_filter,
        show_ooh=show_ooh,
        today=today_str,
        total=len(calls),
    )

@app.route('/rtm/call-log/export')
def rtm_call_log_export():
    auth = _require_auth()
    if auth: return auth
    import csv, io
    from flask import Response
    if not _rtm:
        return "RTM module not available", 500

    today      = _rtm._today_est()
    today_str  = today.isoformat()
    rows_raw   = _rtm._read_sheet(client, _rtm.CALL_STATUS_ID)

    lob_filter      = request.args.get('lob',      '').strip()
    lang_filter     = request.args.get('lang',     '').strip()
    interval_filter = request.args.get('interval', '').strip()
    queue_filter    = request.args.get('queue_id', '').strip()
    show_ooh        = request.args.get('ooh', '0') == '1'

    calls = []
    for r in rows_raw:
        dc = str(r.get("date_created", "") or "").strip()
        dt_cst = _rtm._parse_datetime_str(dc)
        if not dt_cst:
            continue
        dt_est = _rtm._cp_to_est(dt_cst)
        if dt_est.date() != today:
            continue
        try:
            qid = int(r.get("queue_id", 0) or 0)
        except:
            qid = 0
        queue_info = _rtm.QUEUE_MAP.get(qid)
        if not queue_info:
            continue
        lob, lang = queue_info
        in_biz = _rtm._in_business_hours(dt_est, lob)
        if not show_ooh and not in_biz:
            continue
        try:
            qt = float(r.get("queue_time", 0) or 0)
        except:
            qt = 0
        is_aband  = 1 if str(r.get("is_abandoned",   "")).strip() in ("1", "1.0") else 0
        is_rolled = 1 if str(r.get("is_rolled_over", "")).strip() in ("1", "1.0") else 0
        bucket_min = (dt_est.minute // 30) * 30
        interval   = dt_est.replace(minute=bucket_min, second=0, microsecond=0).strftime("%H:%M:%S")
        if lob_filter      and lob      != lob_filter:      continue
        if lang_filter     and lang     != lang_filter:      continue
        if interval_filter and interval != interval_filter:  continue
        if queue_filter    and str(qid) != queue_filter:     continue
        calls.append({
            "date_created":   dt_est.strftime("%Y-%m-%d %H:%M:%S"),
            "log_id":         str(r.get("log_id","") or ""),
            "queue_id":       qid,
            "lob":            lob,
            "language":       lang,
            "interval":       interval,
            "is_abandoned":   is_aband,
            "is_rolled_over": is_rolled,
            "queue_time_sec": int(qt) if qt else 0,
            "duration_sec":   int(float(r.get("duration", 0) or 0)),
            "in_biz":         "Yes" if in_biz else "No (OOH)",
        })

    output = io.StringIO()
    fields = ["date_created","log_id","queue_id","lob","language","interval",
              "is_abandoned","is_rolled_over","queue_time_sec","duration_sec","in_biz"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(calls)
    fname = f"call_log_{today_str}.csv"
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": f"attachment;filename={fname}"})

@app.route('/eod-insights')
def eod_insights():
    auth = _require_auth()
    if auth: return auth
    return render_template('eod_insights.html')

@app.route('/api/eod-insights/prefill')
def eod_insights_prefill():
    auth = _require_auth()
    if auth: return auth
    if not _rtm:
        return jsonify({"error": "RTM module not loaded"}), 500

    threshold = float(request.args.get("threshold", 80))
    today = _rtm._today_est()

    # ── Interval report — aggregate by LOB (core LOBs only for EOD) ──
    interval_rows = _rtm.get_interval_report(today, gc=client)
    lob_agg = {}
    for r in interval_rows:
        if not r["in_biz"]:
            continue
        lob = r["lob"]
        # EOD insights shows core LOBs only — skip EN/FR language splits
        if lob.endswith(' EN') or lob.endswith(' FR'):
            continue
        if lob not in lob_agg:
            lob_agg[lob] = {"calls_offered": 0, "answered_within": 0,
                             "calls_abandoned": 0, "fc_offered": 0}
        lob_agg[lob]["calls_offered"]   += r["calls_offered"]
        lob_agg[lob]["answered_within"] += r["answered_within"]
        lob_agg[lob]["calls_abandoned"] += r["calls_abandoned"]
        lob_agg[lob]["fc_offered"]      += r["forecasted_cv"]

    cascades = _rtm.get_cascades(today, gc=client, override_raw_rows=_get_agent_events_backup(today.isoformat()))
    cascade_by_lob = {}
    for c in cascades:
        cascade_by_lob[c["lob"]] = cascade_by_lob.get(c["lob"], 0) + 1

    interval_by_lob = []
    for lob, d in sorted(lob_agg.items()):
        offered = d["calls_offered"]
        if offered == 0:
            continue
        aw    = d["answered_within"]
        aband = d["calls_abandoned"]
        fc    = d["fc_offered"]
        casc_cnt = cascade_by_lob.get(lob, 0)
        sl        = round(aw / offered * 100, 1)
        otf       = round(offered / fc * 100, 1) if fc > 0 else None
        aband_pct = round(aband / offered * 100, 1)
        casc_rate = round(casc_cnt / offered * 100, 1)
        interval_by_lob.append({
            "lob": lob, "svl": sl, "otf": otf,
            "cascade_rate": casc_rate, "abandoned_pct": aband_pct,
        })

    # ── Absences + VTO per LOB ──
    schedule_rows = _rtm.get_schedule_data(today, gc=client,
                        override_raw_agent_rows=_get_agent_events_backup(today.isoformat()))
    absen_summary = _rtm.get_absenteeism_summary(schedule_rows)
    absen_by_lob  = {s["lob"]: s for s in absen_summary}

    missed = []
    for r in interval_by_lob:
        if r["svl"] < threshold:
            abs_info = absen_by_lob.get(r["lob"], {})
            missed.append({
                "lob":       r["lob"],
                "svl":       r["svl"],
                "otf":       r["otf"] if r["otf"] is not None else "",
                "cascades":  r["cascade_rate"],
                "abandoned": r["abandoned_pct"],
                "absences":  abs_info.get("absence_pct", ""),
                "vto":       abs_info.get("total_vto", ""),
            })

    missed.sort(key=lambda x: x["svl"])
    return jsonify({"missed": missed, "threshold": threshold})

@app.route('/rtm/svl')
def rtm_svl():
    auth = _require_auth()
    if auth: return auth
    call_summary = (_rtm.get_call_status_summary(_rtm._today_est(), gc=client)
                    if _rtm else [])
    lob_summary, _ = (_rtm.get_forecast_data(_rtm._today_est(), gc=client)
                      if _rtm else ([], []))
    fc_by_lob = {r['lob']: r for r in lob_summary}

    # Merge call summary with forecast for SVL view
    rows = []
    all_lobs = sorted(set(
        [r['lob'] for r in call_summary] + list(fc_by_lob.keys())
    ))
    for lob in all_lobs:
        # Aggregate EN+FR for this LOB
        cs = [r for r in call_summary if r['lob'] == lob]
        offered   = sum(r['calls_offered']   for r in cs)
        aw        = sum(r['answered_within'] for r in cs)
        abandoned = sum(r['abandoned']       for r in cs)
        fc        = fc_by_lob.get(lob, {})
        fc_daily  = fc.get('daily_cv', 0)
        fc_utonow = fc.get('utonow_cv', 0)
        sl        = round(aw / offered * 100, 1) if offered > 0 else None
        otf       = round(offered / fc_utonow * 100, 1) if fc_utonow > 0 else None
        aband_pct = round(abandoned / offered * 100, 1) if offered > 0 else None
        rows.append({
            'lob':         lob,
            'fc_daily':    fc_daily,
            'fc_utonow':   fc_utonow,
            'offered':     offered,
            'aw':          aw,
            'abandoned':   abandoned,
            'aband_pct':   aband_pct,
            'service_level': sl,
            'otf':         otf,
        })

    picker_lobs = [r['lob'] for r in rows]
    selected_lobs = request.args.getlist('lob')
    if selected_lobs:
        rows = [r for r in rows if r['lob'] in selected_lobs]

    total_offered = sum(r['offered'] for r in rows)
    total_aw      = sum(r['aw']      for r in rows)
    total_aband   = sum(r['abandoned'] for r in rows)
    total_sl      = round(total_aw / total_offered * 100, 1) if total_offered else None
    total_fc      = sum(r['fc_utonow'] for r in rows)
    total_otf     = round(total_offered / total_fc * 100, 1) if total_fc else None

    return render_template('rtm_svl.html',
        rows=rows,
        total_offered=total_offered,
        total_aw=total_aw,
        total_aband=total_aband,
        total_sl=total_sl,
        total_otf=total_otf,
        all_lobs=picker_lobs,
        selected_lobs=selected_lobs,
        today=_rtm._today_est().isoformat(),
        now=_rtm._now_est().strftime('%H:%M'),
    )


@app.route('/rtm/cascade-debug')
def rtm_cascade_debug():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403
    if not _rtm: return "RTM not available", 500

    today     = _rtm._today_est()
    today_str = today.strftime("%Y-%m-%d")
    rows      = _rtm._read_sheet(client, _rtm.AGENT_STATUS_ID)

    # Build raw events for today
    by_uid = {}
    for r in rows:
        uid = str(r.get("user_id","") or "").strip()
        st  = str(r.get("start_time","") or "").strip()
        sid = str(r.get("status_id","") or "").strip()
        if not uid or not st: continue
        dt_cst = _rtm._parse_datetime_str(st)
        if not dt_cst: continue
        dt_est = _rtm._cp_to_est(dt_cst)
        if dt_est.date() != today: continue
        status = _rtm.CP_STATUS_MAP.get(sid, sid[:20] if sid else "Unknown")
        if uid not in by_uid: by_uid[uid] = []
        by_uid[uid].append({"dt": dt_est, "status": status, "sid": sid})

    # Roster
    roster_rows = _rtm._read_sheet(client, _rtm.ROSTER_SHEET_ID, tab="EMPLOYEES")
    uid_to_info = {}
    for r in roster_rows:
        cid = str(r.get("Current ID","") or "").strip()
        if not cid or cid in _rtm.EXCLUDED_CURRENT_IDS: continue
        fname = str(r.get("First Name","") or "").strip()
        lname = str(r.get("Last Name","")  or "").strip()
        pu    = str(r.get("Planning Unit","") or "").strip()
        lob   = _rtm.PU_TO_LOB.get(pu, pu)
        uid_to_info[cid] = {"name": f"{fname} {lname}".strip(), "lob": lob}

    out = [f"<h2>Cascade Debug — {today_str}</h2>",
           f"<p>Uids today: {len(by_uid)} | Roster: {len(uid_to_info)}</p>",
           f"<p>Range: {_rtm.CASCADE_MIN_SEC}–{_rtm.CASCADE_MAX_SEC}s | Next exclude: {_rtm.CASCADE_NEXT_EXCLUDE}</p>",
           "<h3>Unavailable events 18–26s (near-cascade window)</h3>"]

    found = []
    for uid, events in by_uid.items():
        events.sort(key=lambda x: x["dt"])
        info = uid_to_info.get(uid, {"name": f"UID {uid}", "lob": "Unknown"})
        for i, ev in enumerate(events):
            if ev["status"] != "Unavailable": continue
            if i + 1 >= len(events): continue
            dur = (events[i+1]["dt"] - ev["dt"]).total_seconds()
            if not (18 <= dur <= 26): continue
            next_s = events[i+1]["status"]
            prev_s = events[i-1]["status"] if i > 0 else ""
            is_cascade = (_rtm.CASCADE_MIN_SEC <= dur < _rtm.CASCADE_MAX_SEC
                          and next_s not in _rtm.CASCADE_NEXT_EXCLUDE)
            found.append(
                f"<p>{'🔴 CASCADE' if is_cascade else '⚪ not cascade'} | "
                f"{info['name']} | {ev['dt'].strftime('%H:%M:%S')} | "
                f"Unavail {dur:.0f}s → next={next_s} | prev={prev_s} | SID={ev['sid']}</p>"
            )
    out.extend(found if found else ["<p>No near-cascade events found today</p>"])
    return "\n".join(out)

@app.route('/rtm/cascades')
def rtm_cascades():
    auth = _require_auth()
    if auth: return auth
    today_str_cc = _rtm._today_est().isoformat() if _rtm else ""
    cascades   = _rtm.get_cascades(_rtm._today_est(), gc=client,
                     override_raw_rows=_get_agent_events_backup(today_str_cc)) if _rtm else []
    selected_lobs = request.args.getlist('lob')
    name_filter = request.args.get('name', '').strip().lower()
    show_ooh    = request.args.get('ooh', '0') == '1'
    all_lobs    = sorted(set(c['lob'] for c in cascades))
    all_names   = sorted(set(c['name'] for c in cascades))
    if not show_ooh:
        cascades = [c for c in cascades if c['in_biz']]
    if selected_lobs:
        cascades = [c for c in cascades if c['lob'] in selected_lobs]
    if name_filter:
        cascades = [c for c in cascades if name_filter in c['name'].lower()]
    # Summary by agent — use calls_offered from agent status events
    agent_summary = {}
    for c in cascades:
        n = c['name']
        if n not in agent_summary:
            agent_summary[n] = {'name': n, 'lob': c['lob'], 'count': 0,
                                 'calls_offered': c.get('calls_offered', 0)}
        agent_summary[n]['count'] += 1
        agent_summary[n]['calls_offered'] = max(
            agent_summary[n]['calls_offered'], c.get('calls_offered', 0)
        )
    agent_summary = sorted(agent_summary.values(), key=lambda x: -x['count'])
    for a in agent_summary:
        a['cascade_pct'] = round(a['count'] / a['calls_offered'] * 100, 1) if a['calls_offered'] else None
    return render_template('rtm_cascades.html',
        cascades=cascades,
        agent_summary=agent_summary,
        all_lobs=all_lobs,
        all_names=all_names,
        selected_lobs=selected_lobs,
        name_filter=name_filter,
        show_ooh=show_ooh,
        total=len(cascades),
        today=_rtm._today_est().isoformat(),
        now=_rtm._now_est().strftime('%H:%M'),
    )


@app.route('/api/rtm_agent_detail')
def api_rtm_agent_detail():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({"error": "uid required"}), 400
    try:
        data = _rtm.get_agent_day_detail(gc=client, user_id=uid) if _rtm else {}
        return jsonify(data)
    except Exception as e:
        log.exception("rtm_agent_detail error")
        return jsonify({"error": str(e)}), 500


@app.route('/rtm/efficiency')
@app.route('/rtm/efficiency')
def rtm_efficiency():
    auth = _require_auth()
    if auth: return auth
    date_str = request.args.get('date', '')
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else _rtm._today_est()
    except ValueError:
        target_date = _rtm._today_est()
    rows = _rtm.get_production_efficiency(target_date, gc=client) if _rtm else []

    # LOB summary
    from collections import defaultdict
    lob_map = defaultdict(lambda: {"sched_min":0,"prod_min":0,"count":0})
    for r in rows:
        lb = lob_map[r["lob"]]
        lb["sched_min"] += r["sched_min"]
        lb["prod_min"]  += r["prod_min"]
        lb["count"]     += 1
    lob_summary = []
    for lob, d in sorted(lob_map.items()):
        eff = round(d["prod_min"] / d["sched_min"] * 100, 1) if d["sched_min"] > 0 else None
        lob_summary.append({"lob": lob, "count": d["count"],
                             "sched_min": round(d["sched_min"], 1),
                             "prod_min":  round(d["prod_min"],  1),
                             "eff_pct":   eff})

    is_today = (target_date == _rtm._today_est())
    return render_template('rtm_efficiency.html',
        rows=rows, lob_summary=lob_summary,
        target_date=target_date.strftime('%Y-%m-%d'),
        is_today=is_today)

@app.route('/api/efficiency_data')
def api_efficiency_data():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    date_str = request.args.get('date', '')
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else _rtm._today_est()
    except ValueError:
        target_date = _rtm._today_est()
    rows = _rtm.get_production_efficiency(target_date, gc=client) if _rtm else []
    return jsonify({"rows": rows, "date": target_date.strftime('%Y-%m-%d')})


@app.route('/rtm/live')
def rtm_live():
    auth = _require_auth()
    if auth: return auth
    return render_template('rtm_live.html')

@app.route('/api/rtm_live_agents')
def api_rtm_live_agents():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        result = _rtm.get_live_agents_by_lob(gc=client) if _rtm else {"by_lob": {}, "pre_shift": []}
        return jsonify(result)
    except Exception as e:
        log.exception("rtm_live_agents error")
        return jsonify({"error": str(e)}), 500


@app.route('/rtm/agent-status')
def rtm_agent_status():
    auth = _require_auth()
    if auth: return auth

    # Full history — all status events today, not just current
    today_str_as = _rtm._today_est().isoformat() if _rtm else ""
    all_events = _rtm.get_agent_status_history(gc=client,
                     override_raw_rows=_get_agent_events_backup(today_str_as)) if _rtm else []

    all_names    = sorted(set(e['name']   for e in all_events))
    all_lobs     = sorted(set(e['lob']    for e in all_events))
    all_statuses = sorted(set(e['status'] for e in all_events))

    selected_names     = request.args.getlist('name')
    selected_lobs      = request.args.getlist('lob')
    selected_statuses  = request.args.getlist('statuses')

    filtered = all_events
    if selected_lobs:
        filtered = [e for e in filtered if e['lob'] in selected_lobs]
    if selected_names:
        filtered = [e for e in filtered if e['name'] in selected_names]
    if selected_statuses:
        filtered = [e for e in filtered if e['status'] in selected_statuses]

    return render_template('rtm_agent_status.html',
        agents=filtered,
        all_names=all_names,
        all_lobs=all_lobs,
        all_statuses=all_statuses,
        selected_statuses=selected_statuses,
        selected_lobs=selected_lobs,
        selected_names=selected_names,
        total=len(all_events),
        today=_rtm._today_est().isoformat(),
        now=_rtm._now_est().strftime('%H:%M'),
    )


@app.route('/rtm/agent-debug')
def rtm_agent_debug():
    auth = _require_auth()
    if auth: return auth
    if session.get('user_email') not in ADMIN_USERS:
        return "Admin only", 403
    out = ["<h2>Agent Status Raw Sheet Debug</h2>"]
    gc = client
    rows = _rtm._read_sheet(gc, _rtm.AGENT_STATUS_ID)
    if not rows:
        return "No rows"
    out.append(f"<p>Total rows: {len(rows)}</p>")
    out.append(f"<p>All columns: {list(rows[0].keys())}</p>")
    
    today_str = _rtm._today_est().strftime("%Y-%m-%d")
    today_rows = [r for r in rows if str(r.get("start_time","")).startswith(today_str)]
    out.append(f"<p>Rows for today: {len(today_rows)}</p>")
    if today_rows:
        out.append(f"<p>Sample row 1: {today_rows[0]}</p>")
        out.append(f"<p>Sample row 2: {today_rows[1] if len(today_rows)>1 else ''}</p>")
        out.append(f"<p>Sample row 3: {today_rows[2] if len(today_rows)>2 else ''}</p>")
    
    # Check for duration column
    dur_cols = [k for k in rows[0].keys() if 'dur' in k.lower() or 'sec' in k.lower() or 'time' in k.lower()]
    out.append(f"<p>Duration/time related columns: {dur_cols}</p>")
    
    # Check unique status names
    status_col = None
    for k in rows[0].keys():
        if 'activity' in k.lower() or 'status' in k.lower() or 'sid' in k.lower():
            pass
    # Show sample of unique c_activity_sid vs decoded status
    sids = set(str(r.get("c_activity_sid","")) for r in today_rows[:50])
    out.append(f"<p>Unique c_activity_sids today: {len(sids)}</p>")
    for sid in list(sids)[:5]:
        decoded = _rtm.CP_STATUS_MAP.get(sid, "UNMAPPED")
        out.append(f"<p>&nbsp;&nbsp;{sid[:40]} → {decoded}</p>")
    
    # Check Unavailable rows and calculate duration
    today_str2 = _rtm._today_est().strftime("%Y-%m-%d")
    
    # Group by user_id, sort by time, calculate duration
    from collections import defaultdict
    by_uid = defaultdict(list)
    for r in rows:
        uid = str(r.get("user_id","")).strip()
        st  = str(r.get("start_time","")).strip()
        sid = str(r.get("c_activity_sid","")).strip()
        if not st.startswith(today_str2):
            continue
        dt_cst = _rtm._parse_datetime_str(st)
        if not dt_cst:
            continue
        dt_est = _rtm._cp_to_est(dt_cst)
        status = _rtm.CP_STATUS_MAP.get(sid, "Unknown")
        by_uid[uid].append({"dt": dt_est, "status": status})
    
    # Find Unavailable events with duration
    cascade_candidates = []
    for uid, events in by_uid.items():
        if uid in _rtm.EXCLUDED_CURRENT_IDS:
            continue
        events.sort(key=lambda x: x["dt"])
        for i, ev in enumerate(events):
            if ev["status"] != "Unavailable":
                continue
            if i + 1 >= len(events):
                continue
            dur = (events[i+1]["dt"] - ev["dt"]).total_seconds()
            prev_s = events[i-1]["status"] if i > 0 else ""
            next_s = events[i+1]["status"]
            cascade_candidates.append({
                "uid": uid, "time": ev["dt"].strftime("%H:%M:%S"),
                "dur": dur, "prev": prev_s, "next": next_s
            })
    
    out.append(f"<p>Total Unavailable events with next row: {len(cascade_candidates)}</p>")
    
    # Show duration distribution
    durs = [c["dur"] for c in cascade_candidates]
    if durs:
        out.append(f"<p>Duration range: min={min(durs):.1f}s, max={max(durs):.1f}s</p>")
        # Show full distribution
        import collections
        dur_buckets = collections.Counter(int(c["dur"]) for c in cascade_candidates)
        out.append(f"<p>Duration distribution (top 10): {dict(sorted(dur_buckets.items())[:10])}</p>")
        out.append(f"<p>Status breakdown of Unavailable events:")
        prev_counts = collections.Counter(c["prev"] for c in cascade_candidates)
        next_counts = collections.Counter(c["next"] for c in cascade_candidates)
        out.append(f" prev={dict(prev_counts)}, next={dict(next_counts)}</p>")
        within_range = [c for c in cascade_candidates if 20 <= c["dur"] < 25]
        out.append(f"<p>Duration 20-24s: {len(within_range)}</p>")
        NEXT_EXCLUDE = {"Ready", "On-call", "No-Answer"}
        excluded = [c for c in within_range if c["next"] in NEXT_EXCLUDE]
        out.append(f"<p>20-24s excluded (next=Ready/On-call/No-Answer): {len(excluded)}</p>")
        cascades = [c for c in within_range if c["next"] not in NEXT_EXCLUDE]
        out.append(f"<p>TRUE CASCADES (20-24s, next not Ready/On-call/No-Answer): {len(cascades)}</p>")
        for c in cascades[:5]:
            out.append(f"<p>&nbsp;&nbsp;uid={c['uid']} time={c['time']} dur={c['dur']:.1f}s prev={c['prev']} next={c['next']}</p>")
        
        # Show the 20-24s events in detail regardless of exclusion
        out.append(f"<h3>All 20-24s Unavailable Events (detail)</h3>")
        for c in within_range[:20]:
            out.append(f"<p>uid={c['uid']} | time={c['time']} | dur={c['dur']:.1f}s | prev=<b>{c['prev']}</b> | next=<b>{c['next']}</b></p>")
        
        # Also show full event sequence for first agent with 20-24s event
        if within_range:
            target_uid = within_range[0]['uid']
            out.append(f"<h3>Full event sequence for uid={target_uid}</h3>")
            events_for_uid = sorted(
                [{"dt": _rtm._cp_to_est(_rtm._parse_datetime_str(str(r.get("start_time","")))),
                  "status": _rtm.CP_STATUS_MAP.get(str(r.get("c_activity_sid","")), "?")}
                 for r in rows 
                 if str(r.get("user_id","")) == str(target_uid) 
                 and str(r.get("start_time","")).startswith(today_str2)
                 and _rtm._parse_datetime_str(str(r.get("start_time","")))],
                key=lambda x: x["dt"]
            )
            for e in events_for_uid:
                out.append(f"<p>&nbsp;&nbsp;{e['dt'].strftime('%H:%M:%S')} → {e['status']}</p>")
    
    return "<br>".join(out)
# ========== RUN ==========
if __name__ == "__main__":
    log.info("Starting WFM Flask app. Debug: %s", app.config['DEBUG'])
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=app.config['DEBUG'])
