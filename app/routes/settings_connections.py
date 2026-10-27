"""
routes/settings_connections.py — Connections, Google Sheets, API Connections, Data Feeds
========================================================================================
Split from settings.py for maintainability.
"""

import json
import logging
from datetime import datetime

from flask import request, jsonify, render_template
from app.auth import admin_required, get_current_user

log = logging.getLogger("serevo.settings")


def register(bp, demo_guard, providers):
    """Register all connection-related routes on the settings blueprint."""

    @bp.route("/connections")
    @admin_required
    def connections():
        import json as _json
        user = get_current_user()
        if user and user.get("is_demo"):
            demo_feeds = _json.dumps([
                {"id": 1, "name": "Forecast Import", "sheet_id": "demo-sheet-id",
                 "tab_name": "Forecast", "feed_type": "forecast", "is_active": True,
                 "last_status": "success", "last_run": "2026-10-06 08:00", "last_error": ""},
                {"id": 2, "name": "Actuals Import", "sheet_id": "demo-sheet-id",
                 "tab_name": "Actuals", "feed_type": "actuals", "is_active": True,
                 "last_status": "success", "last_run": "2026-10-06 08:15", "last_error": ""},
            ])
            demo_conns = _json.dumps([
                {"id": 1, "name": "Realtime Feed", "provider": "realtime_api",
                 "base_url": "https://api.example.com", "auth_type": "api_key",
                 "is_active": True, "last_status": "ok", "last_error": "", "last_tested": "2026-10-06 09:00"},
            ])
            return render_template("settings/connections.html",
                user=user, feeds=[], feeds_json=demo_feeds,
                api_conns=[], conns_json=demo_conns,
                providers=providers, global_sa=True)
        from app.models import DataFeed, APIConnection, AppSetting
        feeds = DataFeed.query.order_by(DataFeed.created_at.desc()).all()
        api_conns = APIConnection.query.order_by(APIConnection.created_at.desc()).all()
        feeds_json = _json.dumps([f.to_dict() for f in feeds])
        conns_json = _json.dumps([{
            "id": c.id, "name": c.name, "provider": c.provider,
            "base_url": c.base_url, "auth_type": c.auth_type,
            "is_active": c.is_active,
            "last_status": c.last_status, "last_error": c.last_error or "",
            "last_tested": c.last_tested.strftime("%Y-%m-%d %H:%M") if c.last_tested else None,
        } for c in api_conns])
        global_sa = bool(AppSetting.get("google_service_account_json", ""))
        return render_template("settings/connections.html",
            user=user,
            feeds=feeds, feeds_json=feeds_json,
            api_conns=api_conns, conns_json=conns_json,
            providers=providers,
            global_sa=global_sa,
        )

    # Redirects from old pages to unified Connections page
    @bp.route("/google-sheets")
    @admin_required
    def google_sheets():
        from flask import redirect, url_for
        return redirect(url_for("settings.connections"))

    @bp.route("/api-connections")
    @admin_required
    def api_connections_redirect():
        from flask import redirect, url_for
        return redirect(url_for("settings.connections"))

    @bp.route("/connections/save-global-sa", methods=["POST"])
    @admin_required
    def save_global_service_account():
        """Save or update the global Google service account credentials."""
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, AppSetting
        data = request.get_json(silent=True) or {}
        sa_json = (data.get("service_account_json") or "").strip()

        if not sa_json:
            AppSetting.set("google_service_account_json", "")
            db.session.commit()
            return jsonify({"success": True, "cleared": True})

        try:
            parsed = json.loads(sa_json)
            if "client_email" not in parsed:
                return jsonify({"success": False, "error": "Invalid service account JSON — missing client_email"})
        except (json.JSONDecodeError, ValueError):
            return jsonify({"success": False, "error": "Invalid JSON format"})

        AppSetting.set("google_service_account_json", sa_json)
        db.session.commit()

        import app.data_source as ds
        ds._INSTANCE = None

        return jsonify({"success": True, "email": parsed.get("client_email", "")})

    @bp.route("/google-sheets/save", methods=["POST"])
    @admin_required
    def save_google_sheets():
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, AppSetting
        data = request.get_json(silent=True) or {}
        sheet_key = data.get("sheet_key", "").strip()
        sa_json = data.get("service_account_json", "").strip()

        if not sheet_key:
            return jsonify({"success": False, "error": "Sheet key is required"})

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
        emp_sheet_key = data.get("employee_sheet_key", "").strip()
        if emp_sheet_key:
            AppSetting.set("employee_sheet_key", emp_sheet_key)
        AppSetting.set("google_sheets_status", "configured")
        db.session.commit()

        import app.data_source as ds
        ds._INSTANCE = None

        return jsonify({"success": True})

    def _friendly_sheet_error(e):
        log.warning("Google Sheets error (%s): %s", type(e).__name__, e)
        cls = type(e).__name__.lower()
        msg = str(e)
        low = msg.lower()
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

    @bp.route("/google-sheets/test", methods=["POST"])
    @admin_required
    def test_google_sheets():
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, AppSetting
        from app.data_source import _open_capacity_sheet

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

    @bp.route("/google-sheets/disconnect", methods=["POST"])
    @admin_required
    def disconnect_google_sheets():
        dg = demo_guard()
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

    @bp.route("/api-connections/save", methods=["POST"])
    @admin_required
    def save_connection():
        dg = demo_guard()
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
            if creds:
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

    @bp.route("/api-connections/test", methods=["POST"])
    @admin_required
    def test_connection():
        """Test an API connection using the registered connector (or generic fallback)."""
        dg = demo_guard()
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

    @bp.route("/api-connections/sync", methods=["POST"])
    @admin_required
    def sync_connection():
        """Sync data from an API connection using the connector framework."""
        dg = demo_guard()
        if dg:
            return dg
        from app.connectors.sync import sync_employees, sync_forecasts, sync_schedules

        data = request.get_json(silent=True) or {}
        conn_id = data.get("id")
        sync_type = data.get("type", "employees")
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

    @bp.route("/api-connections/delete", methods=["POST"])
    @admin_required
    def delete_connection():
        dg = demo_guard()
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

    @bp.route("/api-connections/toggle", methods=["POST"])
    @admin_required
    def toggle_connection():
        dg = demo_guard()
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

    # ── Data Feeds ──────────────────────────────────────────────

    @bp.route("/data-feeds")
    @admin_required
    def data_feeds():
        from flask import redirect, url_for
        return redirect(url_for("settings.connections"))

    @bp.route("/data-feeds/save", methods=["POST"])
    @admin_required
    def save_data_feed():
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, DataFeed

        data = request.get_json(silent=True) or {}
        feed_id = data.get("id")
        name = (data.get("name") or "").strip()
        feed_type = (data.get("feed_type") or "").strip()
        source_type = (data.get("source_type") or "google_sheet").strip()
        sheet_key = (data.get("sheet_key") or "").strip()
        sheet_tab = (data.get("sheet_tab") or "").strip()

        if not name:
            return jsonify({"success": False, "error": "Name is required"})
        valid_types = ("call_volume", "agent_status", "planning_data", "employees")
        if feed_type not in valid_types:
            return jsonify({"success": False, "error": f"Feed type must be one of: {', '.join(valid_types)}"})
        if source_type == "google_sheet" and not sheet_key:
            return jsonify({"success": False, "error": "Google Sheet key is required"})

        if feed_id:
            feed = DataFeed.query.get(feed_id)
            if not feed:
                return jsonify({"success": False, "error": "Feed not found"})
        else:
            feed = DataFeed()
            db.session.add(feed)

        feed.name = name
        feed.feed_type = feed_type
        feed.source_type = source_type
        feed.sheet_key = sheet_key
        feed.sheet_tab = sheet_tab
        feed.api_url = (data.get("api_url") or "").strip()
        feed.api_headers = (data.get("api_headers") or "").strip()
        feed.is_active = data.get("is_active", True)

        data_format = (data.get("data_format") or "interval").strip()
        if data_format not in ("interval", "event"):
            data_format = "interval"
        feed.data_format = data_format

        interval_minutes = data.get("interval_minutes")
        if interval_minutes is not None:
            try:
                interval_minutes = int(interval_minutes)
                if interval_minutes not in (5, 10, 15, 30, 60):
                    interval_minutes = 15
            except (ValueError, TypeError):
                interval_minutes = 15
            feed.interval_minutes = interval_minutes

        source_tz = (data.get("source_timezone") or "").strip()
        feed.source_timezone = source_tz

        col_mapping = data.get("column_mapping")
        if col_mapping is not None:
            if isinstance(col_mapping, dict):
                col_mapping = {k: v for k, v in col_mapping.items() if v}
                feed.column_mapping = json.dumps(col_mapping) if col_mapping else ""
            elif isinstance(col_mapping, str):
                feed.column_mapping = col_mapping.strip()
            else:
                feed.column_mapping = ""

        sa_json = (data.get("service_account_json") or "").strip()
        if sa_json:
            try:
                parsed = json.loads(sa_json)
                if "client_email" not in parsed:
                    return jsonify({"success": False, "error": "Invalid service account JSON — missing client_email"})
                feed.service_account_json = sa_json
            except (json.JSONDecodeError, ValueError):
                return jsonify({"success": False, "error": "Invalid JSON format for service account"})
        elif data.get("clear_service_account"):
            feed.service_account_json = ""

        db.session.commit()
        return jsonify({"success": True, "feed": feed.to_dict()})

    @bp.route("/data-feeds/test", methods=["POST"])
    @admin_required
    def test_data_feed():
        from app.models import db, DataFeed
        data = request.get_json(silent=True) or {}
        feed_id = data.get("id")
        feed = DataFeed.query.get(feed_id) if feed_id else None
        if not feed:
            return jsonify({"success": False, "error": "Feed not found"})

        if feed.source_type == "google_sheet":
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                from app.models import AppSetting

                sa_raw = feed.service_account_json or AppSetting.get("google_service_account_json", "")
                if not sa_raw:
                    feed.last_status = "error"
                    feed.last_error = "No service account credentials configured"
                    db.session.commit()
                    return jsonify({"success": False, "error":
                        "No service account credentials. Add them in the feed's edit form, "
                        "or set a global service account on the Connections page."})

                creds = ServiceAccountCredentials.from_json_keyfile_dict(
                    json.loads(sa_raw),
                    ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"])
                gc = gspread.authorize(creds)
                sheet = gc.open_by_key(feed.sheet_key)

                tabs = [ws.title for ws in sheet.worksheets()]

                headers = []
                sample_row = []
                try:
                    ws = sheet.worksheet(feed.sheet_tab) if feed.sheet_tab else sheet.sheet1
                    all_vals = ws.get_all_values()
                    if all_vals:
                        headers = [h for h in all_vals[0] if h.strip()]
                    if len(all_vals) > 1:
                        sample_row = all_vals[1][:len(headers)]
                except Exception:
                    pass

                feed.last_status = "ok"
                feed.last_error = ""
                feed.last_sync_at = datetime.utcnow()
                db.session.commit()
                return jsonify({"success": True, "title": sheet.title, "tabs": tabs,
                                "headers": headers, "sample_row": sample_row})
            except Exception as e:
                err = _friendly_sheet_error(e)
                feed.last_status = "error"
                feed.last_error = err
                db.session.commit()
                return jsonify({"success": False, "error": err})
        else:
            return jsonify({"success": False, "error": "API feed testing not yet implemented"})

    @bp.route("/data-feeds/delete", methods=["POST"])
    @admin_required
    def delete_data_feed():
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, DataFeed
        data = request.get_json(silent=True) or {}
        feed = DataFeed.query.get(data.get("id"))
        if not feed:
            return jsonify({"success": False, "error": "Feed not found"})
        db.session.delete(feed)
        db.session.commit()
        return jsonify({"success": True})

    @bp.route("/data-feeds/toggle", methods=["POST"])
    @admin_required
    def toggle_data_feed():
        dg = demo_guard()
        if dg:
            return dg
        from app.models import db, DataFeed
        data = request.get_json(silent=True) or {}
        feed = DataFeed.query.get(data.get("id"))
        if not feed:
            return jsonify({"success": False, "error": "Feed not found"})
        feed.is_active = not feed.is_active
        db.session.commit()
        return jsonify({"success": True, "is_active": feed.is_active})
