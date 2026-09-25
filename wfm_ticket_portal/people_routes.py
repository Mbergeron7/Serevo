# people_routes.py — LOA + Accommodations module
import uuid, io, csv
from datetime import datetime
from zoneinfo import ZoneInfo
import logging

log = logging.getLogger("wfm_people")
TIMEZONE = "America/Toronto"

LOA_TAB   = "LOA"
ACCOM_TAB = "ACCOMMODATIONS"

LOBS = [
    "SS Sales", "SS Case Management", "PS Sales", "PS Care", "PS Case Manager",
    "Web Leads SS Inbound", "Web Leads SS", "Web Leads PS", "MoveBuddy",
    "I.T Support", "Team Lead", "Admin", "PS Admin", "SS Admin",
    "QA", "Trainer", "EChat", "ERO", "Workforce Management", "Other",
]
LOA_REASONS = ["Maternity", "Paternity", "Medical", "Personal", "Bereavement", "Other"]
ACCOM_TYPES = ["Personal", "Medical", "Business Needs", "Religion", "Other"]
CONTRACTS   = ["FT 40hrs 5x8hr", "PT 32hrs 4x8hr", "PT 20hrs 3x4hr", "PT 24hrs 3x8hr", "Other"]

LOA_HEADERS = [
    "id", "lob", "name", "team_lead", "loa_reason",
    "start_date", "return_date", "ems_status", "returned",
    "status", "archived", "notes", "created_at", "updated_at",
]
ACCOM_HEADERS = [
    "id", "lob", "name",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "request_date", "start_date", "end_date",
    "injixo_contract", "status", "accommodation_type", "comments",
    "archived", "created_at", "updated_at",
]

def _now():
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")

def _new_id():
    return str(uuid.uuid4())[:8].upper()

def _get_or_create_tab(doc, tab_name, headers):
    import gspread
    try:
        ws = doc.worksheet(tab_name)
        if not ws.row_values(1):
            ws.append_row(headers)
        return ws
    except gspread.exceptions.WorksheetNotFound:
        # Genuinely missing — safe to create.
        ws = doc.add_worksheet(tab_name, rows="2000", cols=str(len(headers)))
        ws.append_row(headers)
        return ws
    except gspread.exceptions.APIError as e:
        # A transient error (e.g. 429 rate limit) must NOT be treated as
        # "tab missing" — creating would fail with "already exists" and crash
        # the page. If the tab already exists, return it; otherwise re-raise.
        try:
            return doc.worksheet(tab_name)
        except Exception:
            raise e

def _rows_to_dicts(ws):
    all_vals = ws.get_all_values()
    if len(all_vals) < 2:
        return []
    headers = all_vals[0]
    return [dict(zip(headers, row)) for row in all_vals[1:] if any(row)]

def _find_row_by_id(ws, record_id):
    all_vals = ws.get_all_values()
    if len(all_vals) < 2:
        return None, None
    headers = all_vals[0]
    try:
        id_col = headers.index("id")
    except ValueError:
        return None, None
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > id_col and row[id_col].strip() == str(record_id).strip():
            return i, dict(zip(headers, row))
    return None, None

def register_people_routes(app, get_main_doc, require_auth):
    from flask import render_template, request, redirect, url_for, jsonify, Response

    # ══════════════════════════════════
    # LOA — List
    # ══════════════════════════════════
    @app.route('/people/loa')
    def loa_list():
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc:
            return render_template('loa_list.html', rows=[], lobs=LOBS,
                                   loa_reasons=LOA_REASONS, error="Sheet not connected")
        ws   = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        rows = _rows_to_dicts(ws)
        search        = request.args.get("search", "").strip().lower()
        lob_filter    = request.args.get("lob", "").strip()
        status_filter = request.args.get("status", "").strip()
        show_archived = request.args.get("archived", "0") == "1"
        if not show_archived:
            rows = [r for r in rows if r.get("archived", "").lower() != "yes"]
        if lob_filter:
            rows = [r for r in rows if r.get("lob", "") == lob_filter]
        if status_filter:
            rows = [r for r in rows if r.get("status", "").lower() == status_filter.lower()]
        if search:
            rows = [r for r in rows if search in r.get("name", "").lower()
                    or search in r.get("team_lead", "").lower()]
        from collections import Counter
        from datetime import date as _date
        today = _date.today()
        name_counts = Counter(r["name"].strip().lower() for r in rows if r.get("name"))
        for r in rows:
            r["_is_duplicate"] = name_counts[r.get("name", "").strip().lower()] > 1
            # Calculate days until return date
            rd = r.get("return_date", "").strip()
            if rd and r.get("status", "").lower() == "active" and r.get("returned", "").lower() != "yes":
                try:
                    r["days_left"] = (_date.fromisoformat(rd) - today).days
                except:
                    r["days_left"] = None
            else:
                r["days_left"] = None
        # Sort: overdue first, then by days_left ascending, then by name
        rows.sort(key=lambda r: (
            0 if r.get("days_left") is not None and r["days_left"] < 0 else
            1 if r.get("days_left") is not None and r["days_left"] <= 7 else 2,
            r.get("days_left") if r.get("days_left") is not None else 9999,
            r.get("name", "")
        ))
        return render_template('loa_list.html',
            rows=rows, lobs=LOBS, loa_reasons=LOA_REASONS,
            search=request.args.get("search", ""),
            lob_filter=lob_filter, status_filter=status_filter,
            show_archived=show_archived, total=len(rows))

    # ══════════════════════════════════
    # LOA — New / Edit
    # ══════════════════════════════════
    @app.route('/people/loa/new', methods=['GET', 'POST'])
    def loa_new():
        auth = require_auth()
        if auth: return auth
        if request.method == 'GET':
            return render_template('loa_form.html', record=None,
                                   lobs=LOBS, loa_reasons=LOA_REASONS, mode='new')
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws  = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        now = _now()
        ws.append_row([
            _new_id(),
            request.form.get("lob", ""),
            request.form.get("name", "").strip(),
            request.form.get("team_lead", "").strip(),
            request.form.get("loa_reason", ""),
            request.form.get("start_date", ""),
            request.form.get("return_date", ""),
            request.form.get("ems_status", "Active"),
            request.form.get("returned", "No"),
            request.form.get("status", "Active"),
            "No", request.form.get("notes", "").strip(), now, now,
        ])
        return redirect(url_for('loa_list'))

    @app.route('/people/loa/edit/<record_id>', methods=['GET', 'POST'])
    def loa_edit(record_id):
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        row_idx, record = _find_row_by_id(ws, record_id)
        if not record: return "Record not found", 404
        if request.method == 'GET':
            return render_template('loa_form.html', record=record,
                                   lobs=LOBS, loa_reasons=LOA_REASONS, mode='edit')
        headers = ws.row_values(1)
        for field, val in {
            "lob": request.form.get("lob",""), "name": request.form.get("name","").strip(),
            "team_lead": request.form.get("team_lead","").strip(),
            "loa_reason": request.form.get("loa_reason",""),
            "start_date": request.form.get("start_date",""),
            "return_date": request.form.get("return_date",""),
            "ems_status": request.form.get("ems_status","Active"),
            "returned": request.form.get("returned","No"),
            "status": request.form.get("status","Active"),
            "notes": request.form.get("notes","").strip(),
            "updated_at": _now(),
        }.items():
            if field in headers:
                ws.update_cell(row_idx, headers.index(field)+1, val)
        return redirect(url_for('loa_list'))

    # ══════════════════════════════════
    # LOA — Actions (deactivate/activate/archive/delete)
    # ══════════════════════════════════
    @app.route('/people/loa/action', methods=['POST'])
    def loa_action():
        auth = require_auth()
        if auth: return jsonify({"error":"Not authenticated"}), 403
        doc = get_main_doc()
        if not doc: return jsonify({"error":"Sheet not connected"}), 500
        ws = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        payload   = request.get_json(silent=True) or {}
        action    = payload.get("action","")
        record_id = payload.get("id","")
        row_idx, record = _find_row_by_id(ws, record_id)
        if not record: return jsonify({"error":"Not found"}), 404
        headers = ws.row_values(1)
        def _set(field, val):
            if field in headers:
                ws.update_cell(row_idx, headers.index(field)+1, val)
            if "updated_at" in headers:
                ws.update_cell(row_idx, headers.index("updated_at")+1, _now())
        if   action == "deactivate": _set("status", "Inactive")
        elif action == "activate":   _set("status", "Active")
        elif action == "archive":    _set("archived","Yes"); _set("status","Inactive")
        elif action == "delete":     ws.delete_rows(row_idx)
        else: return jsonify({"error":"Unknown action"}), 400
        return jsonify({"success": True})

    # ══════════════════════════════════
    # LOA — Import (batch from XLSX parsed client-side)
    # ══════════════════════════════════
    @app.route('/people/loa/import', methods=['POST'])
    def loa_import():
        auth = require_auth()
        if auth: return jsonify({"error":"Not authenticated"}), 403
        doc = get_main_doc()
        if not doc: return jsonify({"error":"Sheet not connected"}), 500
        ws = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        records = request.get_json(silent=True) or []
        if not isinstance(records, list):
            return jsonify({"error":"Invalid data"}), 400
        now = _now()
        imported = skipped = 0
        for r in records:
            name = str(r.get("name","") or "").strip()
            if not name:
                skipped += 1
                continue
            ws.append_row([
                _new_id(),                                          # auto-generated id
                str(r.get("lob","")         or "").strip(),
                name,
                str(r.get("team_lead","")   or "").strip(),
                str(r.get("loa_reason","")  or "").strip(),
                str(r.get("start_date","")  or "").strip(),
                str(r.get("return_date","") or "").strip(),
                str(r.get("ems_status","Active") or "Active").strip(),
                str(r.get("returned","No")  or "No").strip(),
                "Active", "No",                                    # status, archived
                str(r.get("notes","")       or "").strip(),
                now, now,
            ])
            imported += 1
        return jsonify({"imported": imported, "skipped": skipped})

    # ══════════════════════════════════
    # LOA — Export CSV
    # ══════════════════════════════════
    @app.route('/people/loa/export')
    def loa_export():
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws   = _get_or_create_tab(doc, LOA_TAB, LOA_HEADERS)
        rows = _rows_to_dicts(ws)
        show_archived = request.args.get("archived", "0") == "1"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=LOA_HEADERS, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            if not show_archived and r.get("archived", "").lower() == "yes":
                continue
            writer.writerow(r)
        fname = "LOA_export_all.csv" if show_archived else "LOA_export.csv"
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={"Content-Disposition": "attachment;filename=" + fname})

    # ══════════════════════════════════════════════════════════════════════════
    # ACCOMMODATIONS — List
    # ══════════════════════════════════════════════════════════════════════════
    @app.route('/people/accommodations')
    def accom_list():
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc:
            return render_template('accom_list.html', rows=[], lobs=LOBS,
                                   accom_types=ACCOM_TYPES, error="Sheet not connected")
        ws   = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        rows = _rows_to_dicts(ws)
        search        = request.args.get("search","").strip().lower()
        lob_filter    = request.args.get("lob","").strip()
        status_filter = request.args.get("status","").strip()
        show_archived = request.args.get("archived","0") == "1"
        if not show_archived:
            rows = [r for r in rows if r.get("archived","").lower() != "yes"]
        if lob_filter:
            rows = [r for r in rows if r.get("lob","") == lob_filter]
        if status_filter:
            rows = [r for r in rows if r.get("status","").lower() == status_filter.lower()]
        if search:
            rows = [r for r in rows if search in r.get("name","").lower()]
        from datetime import date as _date2
        today2 = _date2.today()
        for r in rows:
            ed = r.get("end_date", "").strip()
            if ed and r.get("status", "").lower() == "active":
                try:
                    r["days_left"] = (_date2.fromisoformat(ed) - today2).days
                except:
                    r["days_left"] = None
            else:
                r["days_left"] = None
        # Sort: overdue first, then soonest, then alphabetical
        rows.sort(key=lambda r: (
            0 if r.get("days_left") is not None and r["days_left"] < 0 else
            1 if r.get("days_left") is not None and r["days_left"] <= 7 else 2,
            r.get("days_left") if r.get("days_left") is not None else 9999,
            r.get("name", "")
        ))
        return render_template('accom_list.html',
            rows=rows, lobs=LOBS, accom_types=ACCOM_TYPES, contracts=CONTRACTS,
            search=request.args.get("search",""),
            lob_filter=lob_filter, status_filter=status_filter,
            show_archived=show_archived, total=len(rows))

    # ══════════════════════════════════
    # ACCOMMODATIONS — New / Edit
    # ══════════════════════════════════
    @app.route('/people/accommodations/new', methods=['GET', 'POST'])
    def accom_new():
        auth = require_auth()
        if auth: return auth
        if request.method == 'GET':
            return render_template('accom_form.html', record=None,
                                   lobs=LOBS, accom_types=ACCOM_TYPES,
                                   contracts=CONTRACTS, mode='new')
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws  = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        now = _now()
        ws.append_row([
            _new_id(),
            request.form.get("lob",""),
            request.form.get("name","").strip(),
            request.form.get("monday","").strip(),
            request.form.get("tuesday","").strip(),
            request.form.get("wednesday","").strip(),
            request.form.get("thursday","").strip(),
            request.form.get("friday","").strip(),
            request.form.get("saturday","").strip(),
            request.form.get("sunday","").strip(),
            request.form.get("request_date",""),
            request.form.get("start_date",""),
            request.form.get("end_date",""),
            request.form.get("injixo_contract",""),
            request.form.get("status","Active"),
            request.form.get("accommodation_type",""),
            request.form.get("comments","").strip(),
            "No", now, now,
        ])
        return redirect(url_for('accom_list'))

    @app.route('/people/accommodations/edit/<record_id>', methods=['GET', 'POST'])
    def accom_edit(record_id):
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        row_idx, record = _find_row_by_id(ws, record_id)
        if not record: return "Record not found", 404
        if request.method == 'GET':
            return render_template('accom_form.html', record=record,
                                   lobs=LOBS, accom_types=ACCOM_TYPES,
                                   contracts=CONTRACTS, mode='edit')
        headers = ws.row_values(1)
        for field, val in {
            "lob": request.form.get("lob",""),
            "name": request.form.get("name","").strip(),
            "monday": request.form.get("monday","").strip(),
            "tuesday": request.form.get("tuesday","").strip(),
            "wednesday": request.form.get("wednesday","").strip(),
            "thursday": request.form.get("thursday","").strip(),
            "friday": request.form.get("friday","").strip(),
            "saturday": request.form.get("saturday","").strip(),
            "sunday": request.form.get("sunday","").strip(),
            "request_date": request.form.get("request_date",""),
            "start_date": request.form.get("start_date",""),
            "end_date": request.form.get("end_date",""),
            "injixo_contract": request.form.get("injixo_contract",""),
            "status": request.form.get("status","Active"),
            "accommodation_type": request.form.get("accommodation_type",""),
            "comments": request.form.get("comments","").strip(),
            "updated_at": _now(),
        }.items():
            if field in headers:
                ws.update_cell(row_idx, headers.index(field)+1, val)
        return redirect(url_for('accom_list'))

    # ══════════════════════════════════
    # ACCOMMODATIONS — Actions
    # ══════════════════════════════════
    @app.route('/people/accommodations/action', methods=['POST'])
    def accom_action():
        auth = require_auth()
        if auth: return jsonify({"error":"Not authenticated"}), 403
        doc = get_main_doc()
        if not doc: return jsonify({"error":"Sheet not connected"}), 500
        ws = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        payload   = request.get_json(silent=True) or {}
        action    = payload.get("action","")
        record_id = payload.get("id","")
        row_idx, record = _find_row_by_id(ws, record_id)
        if not record: return jsonify({"error":"Not found"}), 404
        headers = ws.row_values(1)
        def _set(field, val):
            if field in headers:
                ws.update_cell(row_idx, headers.index(field)+1, val)
            if "updated_at" in headers:
                ws.update_cell(row_idx, headers.index("updated_at")+1, _now())
        if   action == "deactivate": _set("status","Inactive")
        elif action == "activate":   _set("status","Active")
        elif action == "archive":    _set("archived","Yes"); _set("status","Inactive")
        elif action == "delete":     ws.delete_rows(row_idx)
        else: return jsonify({"error":"Unknown action"}), 400
        return jsonify({"success": True})

    # ══════════════════════════════════
    # ACCOMMODATIONS — Import
    # ══════════════════════════════════
    @app.route('/people/accommodations/import', methods=['POST'])
    def accom_import():
        auth = require_auth()
        if auth: return jsonify({"error":"Not authenticated"}), 403
        doc = get_main_doc()
        if not doc: return jsonify({"error":"Sheet not connected"}), 500
        ws = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        records = request.get_json(silent=True) or []
        if not isinstance(records, list):
            return jsonify({"error":"Invalid data"}), 400
        now = _now()
        imported = skipped = 0
        for r in records:
            name = str(r.get("name","") or "").strip()
            if not name:
                skipped += 1
                continue
            raw_status = str(r.get("status","Active") or "Active").strip()
            status = "Active" if raw_status.lower() in ("active","1","yes") else "Inactive"
            ws.append_row([
                _new_id(),                                          # auto-generated id
                str(r.get("lob","")                or "").strip(),
                name,
                str(r.get("monday","")             or "").strip(),
                str(r.get("tuesday","")            or "").strip(),
                str(r.get("wednesday","")          or "").strip(),
                str(r.get("thursday","")           or "").strip(),
                str(r.get("friday","")             or "").strip(),
                str(r.get("saturday","")           or "").strip(),
                str(r.get("sunday","")             or "").strip(),
                str(r.get("request_date","")       or "").strip(),
                str(r.get("start_date","")         or "").strip(),
                str(r.get("end_date","")           or "").strip(),
                str(r.get("injixo_contract","")    or "").strip(),
                status,
                str(r.get("accommodation_type","") or "").strip(),
                str(r.get("comments","")           or "").strip(),
                "No", now, now,
            ])
            imported += 1
        return jsonify({"imported": imported, "skipped": skipped})

    # ══════════════════════════════════
    # ACCOMMODATIONS — Export CSV
    # ══════════════════════════════════
    @app.route('/people/accommodations/export')
    def accom_export():
        auth = require_auth()
        if auth: return auth
        doc = get_main_doc()
        if not doc: return "Sheet not connected", 500
        ws   = _get_or_create_tab(doc, ACCOM_TAB, ACCOM_HEADERS)
        rows = _rows_to_dicts(ws)
        show_archived = request.args.get("archived", "0") == "1"
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=ACCOM_HEADERS, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            if not show_archived and r.get("archived", "").lower() == "yes":
                continue
            writer.writerow(r)
        fname = "Accommodations_export_all.csv" if show_archived else "Accommodations_export.csv"
        return Response(output.getvalue(), mimetype='text/csv',
                        headers={"Content-Disposition": "attachment;filename=" + fname})
