"""
routes/data_import.py — CSV / Excel bulk upload + Google Sheet sync
===================================================================
Allows admins to upload CSV or Excel files to populate the database
tables (employees, forecast, requirements, accommodations, PTO,
activities, contracts, planning units).

Supports flexible column mapping: each upload type has a set of known
aliases so client files with different header names are auto-matched.
Users can also manually map columns and save the mapping as a reusable
schema profile.
"""

import io
import csv
import json
import os
import logging
import tempfile
import uuid
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, flash, session)
from app.auth import login_required, admin_required, get_current_user
from config import cfg

log = logging.getLogger("serevo.data_import")

data_import_bp = Blueprint("data_import", __name__, url_prefix="/data")


# ── Column alias registry ─────────────────────────────────────
# Maps each internal field name to a list of common aliases that
# clients might use in their CSV/Excel headers.
COLUMN_ALIASES = {
    # ── Employees ──
    "Employee ID":      ["employee id", "emp id", "empid", "agent id", "agentid",
                         "employee number", "emp number", "staff id", "badge",
                         "badge number", "id", "personnel number", "person id"],
    "First Name":       ["first name", "firstname", "given name", "prénom", "fname",
                         "first", "forename"],
    "Last Name":        ["last name", "lastname", "surname", "family name", "nom",
                         "lname", "last"],
    "Status":           ["status", "employment status", "emp status", "active",
                         "state"],
    "LOB":              ["lob", "line of business", "business line", "department",
                         "dept", "queue", "skill group", "team"],
    "Planning Unit":    ["planning unit", "pu", "organisational unit", "org unit",
                         "unit", "site", "location", "center", "centre"],
    "All Skills":       ["all skills", "skills", "skill list", "competencies",
                         "qualifications"],
    "Email":            ["email", "e-mail", "email address", "mail"],
    "Address Email":    ["address email", "work email", "corporate email",
                         "business email"],
    "Contract Type":    ["contract type", "employment type", "work type",
                         "position type"],
    "External ID 1":    ["external id 1", "ext id 1", "external id", "ext id",
                         "peopleware id", "external identifier", "system id"],
    "External ID 2":    ["external id 2", "ext id 2", "secondary id",
                         "alt id", "alternate id"],
    "Languages":        ["languages", "language", "langs", "spoken languages"],
    "Contract":         ["contract", "contract name", "contract template",
                         "work agreement"],
    "Weekly Hours":     ["weekly hours", "hours per week", "contracted hours",
                         "weekly contracted hours"],
    "Days Per Week":    ["days per week", "work days", "working days",
                         "contracted days"],
    "Hours Per Day":    ["hours per day", "daily hours", "shift length"],
    "Timezone":         ["timezone", "time zone", "tz"],
    "Team Lead":        ["team lead", "team leader", "supervisor", "manager",
                         "tl", "lead"],
    "Schedule Excluded": ["schedule excluded", "excluded", "exclude from schedule",
                          "no schedule"],
    "Start Date":       ["start date", "hire date", "date hired", "join date",
                         "employment start", "start"],
    "End Date":         ["end date", "termination date", "leave date",
                         "separation date", "end"],
    "Title":            ["title", "job title", "position", "role"],

    # ── Forecast / Actuals ──
    "Timestamp":        ["timestamp", "datetime", "date time",
                         "interval", "interval start", "period"],
    "Date":             ["date", "call date", "interval date"],
    "Time":             ["time", "interval time", "start time"],
    "Offered":          ["offered", "calls offered", "contacts offered",
                         "volume", "inbound"],
    "AHT":              ["aht", "average handle time", "avg handle time",
                         "handle time"],

    # ── Requirements ──
    "Agents Required":  ["agents required", "required", "fte required",
                         "headcount required", "staffing requirement", "req"],

    # ── Accommodations ──
    "Employee":         ["employee", "employee name", "name", "agent",
                         "agent name", "staff"],
    "Shift Start":      ["shift start", "start time", "shift begin"],
    "Shift End":        ["shift end", "end time", "shift finish"],
    "Notes":            ["notes", "note", "comments", "remarks"],
    "Mon":              ["mon", "monday"],
    "Tue":              ["tue", "tuesday", "tues"],
    "Wed":              ["wed", "wednesday"],
    "Thu":              ["thu", "thursday", "thur", "thurs"],
    "Fri":              ["fri", "friday"],
    "Sat":              ["sat", "saturday"],
    "Sun":              ["sun", "sunday"],

    # ── PTO ──
    "Type":             ["type", "pto type", "leave type", "absence type",
                         "time off type"],
    "Note":             ["note", "reason", "description"],

    # ── Actuals ──
    "Answered":         ["answered", "calls answered", "handled"],
    "Answered Within":  ["answered within", "answered within sl",
                         "service level", "sl answered"],
    "Abandoned":        ["abandoned", "calls abandoned", "abn"],
    "Rolled":           ["rolled", "overflowed", "transferred"],
    "ASA":              ["asa", "avg speed answer", "average speed of answer"],
    "Max Queued":       ["max queued", "max in queue", "peak queue"],

    # ── Agent Status ──
    "Start":            ["start", "start time", "event start"],
    "End":              ["end", "end time", "event end"],

    # ── Activities / Segment Codes ──
    "Code":             ["code", "activity code", "segment code", "act code",
                         "id"],
    "Label":            ["label", "name", "activity name", "description",
                         "display name"],
    "Color":            ["color", "colour", "hex color", "display color"],
    "Activity Type":    ["activity type", "type", "act type"],
    "Activity Category":["activity category", "category", "act category"],
    "Is Productive":    ["is productive", "productive"],
    "Is Paid":          ["is paid", "paid"],
    "Official Name":    ["official name", "full name"],
    "Abbreviation":     ["abbreviation", "abbrev", "abbr", "short name"],
    "Shortcut":         ["shortcut", "key", "hotkey"],
    "External IDs":     ["external ids", "ext ids", "external identifiers"],
    "Parent Code":      ["parent code", "parent", "parent activity"],
    "Is Multi-Activity":["is multi-activity", "multi activity", "multi-activity"],
    "Is Replaceable":   ["is replaceable", "replaceable"],
    "Is Plannable":     ["is plannable", "plannable"],
    "Importance":       ["importance", "weight"],
    "Priority":         ["priority", "rank"],
    "Is Requestable":   ["is requestable", "requestable"],
    "Is Exchangeable":  ["is exchangeable", "exchangeable", "swappable"],
    "Allow Full Day":   ["allow full day", "full day"],
    "Can Be Day Status":["can be day status", "day status"],
    "Offset Mins":      ["offset mins", "offset minutes", "offset"],
    "Duration Mins":    ["duration mins", "duration minutes", "duration"],
    "Is Flexible":      ["is flexible", "flexible"],
    "Window Start Mins":["window start mins", "window start"],
    "Window End Mins":  ["window end mins", "window end"],

    # ── Contracts ──
    "Name":             ["name", "contract name", "template name"],
    "Days Per Week":    ["days per week", "work days per week"],
    "Workdays Calculation": ["workdays calculation", "workday calc"],
    "Daily Hours Min":  ["daily hours min", "min daily hours"],
    "Daily Hours Target": ["daily hours target", "target daily hours"],
    "Daily Hours Max":  ["daily hours max", "max daily hours"],
    "Weekly Hours Min": ["weekly hours min", "min weekly hours"],
    "Weekly Hours Target": ["weekly hours target", "target weekly hours"],
    "Weekly Hours Max": ["weekly hours max", "max weekly hours"],
    "Monthly Hours Max": ["monthly hours max", "max monthly hours"],
    "Break Duration Mins": ["break duration mins", "break duration",
                            "break minutes"],
    "Break After Hours": ["break after hours", "break threshold"],
    "Lunch Duration Mins": ["lunch duration mins", "lunch duration",
                            "lunch minutes"],
    "Overtime Eligible": ["overtime eligible", "ot eligible", "overtime"],
    "Min Rest Hours":   ["min rest hours", "rest period", "rest hours"],
    "Max Consecutive Days": ["max consecutive days", "consecutive days"],
    "Min Days Per Week": ["min days per week", "min working days"],
    "Max Days Per Week": ["max days per week", "max working days"],
    "Min Days Off Per Week": ["min days off per week", "days off"],
    "Max Night Shifts Week": ["max night shifts week", "weekly night shifts"],
    "Max Night Shifts Month": ["max night shifts month", "monthly night shifts"],

    # ── Planning Units ──
    "Description":      ["description", "desc"],
    "Is Active":        ["is active", "active", "enabled"],
    "Mon Open":         ["mon open", "monday open"],
    "Mon Close":        ["mon close", "monday close"],
    "Tue Open":         ["tue open", "tuesday open"],
    "Tue Close":        ["tue close", "tuesday close"],
    "Wed Open":         ["wed open", "wednesday open"],
    "Wed Close":        ["wed close", "wednesday close"],
    "Thu Open":         ["thu open", "thursday open"],
    "Thu Close":        ["thu close", "thursday close"],
    "Fri Open":         ["fri open", "friday open"],
    "Fri Close":        ["fri close", "friday close"],
    "Sat Open":         ["sat open", "saturday open"],
    "Sat Close":        ["sat close", "saturday close"],
    "Sun Open":         ["sun open", "sunday open"],
    "Sun Close":        ["sun close", "sunday close"],

    # ── Skill dates ──
    "Skill Start":      ["skill start", "latest skill start", "skill date"],
    "Skill End":        ["skill end", "latest skill end"],
    "Latest Skill Name":["latest skill name", "current skill", "primary skill"],
}


def _auto_match_columns(file_headers, upload_type):
    """Auto-match file headers to internal fields using the alias registry.

    Returns a dict: {file_header: internal_field_or_None}
    """
    type_info = UPLOAD_TYPES[upload_type]
    all_fields = type_info["required"] + type_info["optional"]

    # Build reverse lookup: alias → internal field (only for fields in this type)
    alias_to_field = {}
    for field in all_fields:
        # The field name itself is always an alias
        alias_to_field[field.lower().replace("_", " ")] = field
        if field in COLUMN_ALIASES:
            for alias in COLUMN_ALIASES[field]:
                alias_to_field[alias.lower()] = field

    matched = {}
    used_fields = set()  # prevent two headers mapping to the same field

    for header in file_headers:
        norm = header.strip().lower().replace("_", " ")
        field = alias_to_field.get(norm)
        if field and field not in used_fields:
            matched[header] = field
            used_fields.add(field)
        else:
            matched[header] = None  # unmatched

    return matched


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    from app.auth import get_current_user
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, demo=True,
                       message="Changes are not saved in demo mode.",
                       imported=0, total=0, skipped=0, errors=[])
    return None


# ── Column mappings per upload type ──────────────────────────
UPLOAD_TYPES = {
    "employees": {
        "label": "Employees",
        "required": ["Employee ID", "First Name", "Last Name"],
        "optional": ["Status", "LOB", "All Skills", "Skill Start", "Skill End", "End Date",
                      "Planning Unit", "Latest Skill Name", "Contract Type", "Email",
                      "Address Email", "Start Date", "Title",
                      "External ID 1", "External ID 2", "Languages", "Contract",
                      "Weekly Hours", "Days Per Week", "Hours Per Day",
                      "Timezone", "Team Lead", "Schedule Excluded"],
    },
    "forecast": {
        "label": "Forecast Data",
        "required": ["LOB", "Offered", "AHT"],
        "optional": ["Timestamp", "Date", "Time"],
    },
    "requirements": {
        "label": "Requirements Data",
        "required": ["Timestamp", "LOB", "Agents Required"],
        "optional": [],
    },
    "accommodations": {
        "label": "Accommodations",
        "required": ["Employee"],
        "optional": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
                      "Shift Start", "Shift End", "Notes"],
    },
    "pto": {
        "label": "PTO / Time Off",
        "required": ["Employee", "Start Date", "End Date"],
        "optional": ["Type", "Note"],
    },
    "actuals": {
        "label": "Call Actuals (ACD intervals)",
        "required": ["LOB", "Offered"],
        "optional": ["Timestamp", "Date", "Time", "Answered", "Answered Within",
                      "Abandoned", "Rolled", "ASA", "AHT", "Max Queued"],
    },
    "agent_status": {
        "label": "Agent Status Events",
        "required": ["Employee ID", "Status", "Start"],
        "optional": ["End"],
    },
    "activities": {
        "label": "Activities / Segment Codes",
        "required": ["Code", "Label"],
        "optional": ["Color", "Activity Type", "Activity Category", "Is Productive", "Is Paid",
                      "Official Name", "Abbreviation", "Shortcut", "External IDs",
                      "Parent Code", "Is Multi-Activity",
                      "Is Replaceable", "Is Plannable", "Importance", "Priority",
                      "Is Requestable", "Is Exchangeable", "Allow Full Day",
                      "Can Be Day Status", "Offset Mins", "Duration Mins",
                      "Is Flexible", "Window Start Mins", "Window End Mins"],
    },
    "contracts": {
        "label": "Contracts",
        "required": ["Name"],
        "optional": ["Abbreviation", "Color", "Contract Type", "Days Per Week",
                      "Workdays Calculation",
                      "Daily Hours Min", "Daily Hours Target", "Daily Hours Max",
                      "Weekly Hours Min", "Weekly Hours Target", "Weekly Hours Max",
                      "Monthly Hours Max",
                      "Break Duration Mins", "Break After Hours",
                      "Lunch Duration Mins", "Overtime Eligible",
                      "Min Rest Hours", "Max Consecutive Days",
                      "Min Days Per Week", "Max Days Per Week",
                      "Min Days Off Per Week", "Max Night Shifts Week",
                      "Max Night Shifts Month"],
    },
    "planning_units": {
        "label": "Planning Units",
        "required": ["Name"],
        "optional": ["Description", "Timezone", "Is Active",
                      "Mon Open", "Mon Close", "Tue Open", "Tue Close",
                      "Wed Open", "Wed Close", "Thu Open", "Thu Close",
                      "Fri Open", "Fri Close", "Sat Open", "Sat Close",
                      "Sun Open", "Sun Close"],
    },
}


@data_import_bp.route("/")
@admin_required
def index():
    user = get_current_user()
    from app.models import DataUpload
    recent = DataUpload.query.order_by(DataUpload.created_at.desc()).limit(20).all()
    return render_template("data_import/index.html",
        user=user,
        upload_types=UPLOAD_TYPES,
        recent_uploads=recent,
    )


@data_import_bp.route("/manual")
@admin_required
def manual_entry():
    user = get_current_user()
    return render_template("data_import/manual_entry.html", user=user)


@data_import_bp.route("/upload", methods=["POST"])
@admin_required
def upload():
    """Legacy direct-import endpoint (backwards compatible).

    If the file headers match our expected columns exactly, imports
    immediately. Otherwise returns a mapping preview for the two-step
    flow.
    """
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    upload_type = request.form.get("upload_type", "")
    if upload_type not in UPLOAD_TYPES:
        return jsonify({"success": False, "error": f"Unknown upload type: {upload_type}"})

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file selected"})

    filename = file.filename.lower()
    try:
        if filename.endswith(".xls") and not filename.endswith(".xlsx"):
            return jsonify({"success": False, "error": "Legacy .xls format is not supported. Please save as .xlsx or .csv."})
        if filename.endswith(".xlsx"):
            rows = _parse_excel(file)
        elif filename.endswith(".csv"):
            rows = _parse_csv(file)
        else:
            return jsonify({"success": False, "error": "Unsupported file type. Use .csv or .xlsx"})
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not parse file: {e}"})

    if not rows:
        return jsonify({"success": False, "error": "File is empty or has no data rows"})

    # Check if a saved schema profile was selected
    schema_id = request.form.get("schema_id")
    if schema_id:
        rows = _apply_schema_mapping(rows, int(schema_id))

    # Validate required columns using alias matching
    type_info = UPLOAD_TYPES[upload_type]
    headers = [h.strip() for h in rows[0].keys()]
    auto_map = _auto_match_columns(headers, upload_type)

    # Check required fields are covered
    matched_fields = {v for v in auto_map.values() if v}
    missing = [c for c in type_info["required"] if c not in matched_fields]
    if missing:
        return jsonify({
            "success": False,
            "error": f"Missing required columns: {', '.join(missing)}. Found: {', '.join(headers)}"
        })

    # Apply the auto-matched aliases: rename headers so import functions work
    rows = _remap_rows(rows, auto_map)

    # Process rows
    log.info("Upload started: type=%s rows=%d file=%s user=%s", upload_type, len(rows), file.filename, user.get("email", ""))
    result = _import_rows(upload_type, rows, user.get("email", ""))
    if result.get("success"):
        log.info("Upload complete: type=%s imported=%d skipped=%d", upload_type, result.get("imported", 0), result.get("skipped", 0))
    else:
        log.error("Upload failed: type=%s error=%s", upload_type, result.get("error", ""))
    return jsonify(result)


# ── Two-step mapping flow ──────────────────────────────────────

@data_import_bp.route("/upload/preview", methods=["POST"])
@admin_required
def upload_preview():
    """Step 1: Parse the file, auto-match columns, return the mapping
    for the user to review and adjust before importing."""
    user = get_current_user()
    upload_type = request.form.get("upload_type", "")
    if upload_type not in UPLOAD_TYPES:
        return jsonify({"success": False, "error": f"Unknown upload type: {upload_type}"})

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file selected"})

    filename = file.filename.lower()
    try:
        if filename.endswith(".xls") and not filename.endswith(".xlsx"):
            return jsonify({"success": False, "error": "Legacy .xls format not supported."})
        if filename.endswith(".xlsx"):
            rows = _parse_excel(file)
        elif filename.endswith(".csv"):
            rows = _parse_csv(file)
        else:
            return jsonify({"success": False, "error": "Use .csv or .xlsx"})
    except Exception as e:
        return jsonify({"success": False, "error": f"Parse error: {e}"})

    if not rows:
        return jsonify({"success": False, "error": "File is empty"})

    # Store rows in a temp file (session cookies are too small for data)
    import_key = str(uuid.uuid4())
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, f"serevo_import_{import_key}.json")
    with open(tmp_path, "w") as tf:
        json.dump({"rows": rows, "upload_type": upload_type,
                    "filename": file.filename}, tf)
    session["_import_key"] = import_key

    headers = [h.strip() for h in rows[0].keys()]
    type_info = UPLOAD_TYPES[upload_type]

    # Check if a saved schema was requested
    schema_id = request.form.get("schema_id")
    if schema_id:
        from app.models import SchemaMapping
        sm = SchemaMapping.query.get(int(schema_id))
        if sm and sm.upload_type == upload_type:
            saved_map = sm.get_mapping()
            # Build auto_map from the saved mapping
            auto_map = {}
            for h in headers:
                auto_map[h] = saved_map.get(h)
        else:
            auto_map = _auto_match_columns(headers, upload_type)
    else:
        auto_map = _auto_match_columns(headers, upload_type)

    matched_fields = {v for v in auto_map.values() if v}
    missing_required = [c for c in type_info["required"] if c not in matched_fields]

    # Sample data (first 3 rows) for each header
    sample_data = {}
    for h in headers:
        vals = []
        for row in rows[:3]:
            v = row.get(h, "")
            if v is not None and str(v).strip():
                vals.append(str(v).strip()[:60])
        sample_data[h] = vals

    # Load saved schemas for this type
    from app.models import SchemaMapping
    saved_schemas = SchemaMapping.query.filter_by(upload_type=upload_type)\
        .order_by(SchemaMapping.updated_at.desc()).all()

    return jsonify({
        "success": True,
        "upload_type": upload_type,
        "filename": file.filename,
        "row_count": len(rows),
        "file_headers": headers,
        "auto_mapping": auto_map,
        "available_fields": {
            "required": type_info["required"],
            "optional": type_info["optional"],
        },
        "missing_required": missing_required,
        "sample_data": sample_data,
        "saved_schemas": [
            {"id": s.id, "name": s.name, "updated_at": s.updated_at.isoformat()}
            for s in saved_schemas
        ],
    })


@data_import_bp.route("/upload/confirm", methods=["POST"])
@admin_required
def upload_confirm():
    """Step 2: Apply the user's confirmed mapping and run the import."""
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No mapping data"})

    mapping = data.get("mapping", {})  # {file_header: internal_field}
    save_as = data.get("save_as")       # optional: name to save this mapping

    import_key = session.pop("_import_key", None)
    if not import_key:
        return jsonify({"success": False,
                        "error": "Session expired — please re-upload the file"})
    tmp_path = os.path.join(tempfile.gettempdir(), f"serevo_import_{import_key}.json")
    try:
        with open(tmp_path, "r") as tf:
            stored = json.load(tf)
        os.remove(tmp_path)
    except FileNotFoundError:
        return jsonify({"success": False,
                        "error": "Session expired — please re-upload the file"})
    rows = stored["rows"]
    upload_type = stored["upload_type"]
    orig_filename = stored.get("filename", "upload")

    if upload_type not in UPLOAD_TYPES:
        return jsonify({"success": False, "error": f"Unknown type: {upload_type}"})

    # Validate required fields are mapped
    type_info = UPLOAD_TYPES[upload_type]
    mapped_fields = {v for v in mapping.values() if v}
    missing = [c for c in type_info["required"] if c not in mapped_fields]
    if missing:
        return jsonify({
            "success": False,
            "error": f"Required fields not mapped: {', '.join(missing)}"
        })

    # Save schema profile if requested
    if save_as:
        from app.models import db, SchemaMapping
        existing = SchemaMapping.query.filter_by(
            name=save_as, upload_type=upload_type
        ).first()
        if existing:
            existing.set_mapping(mapping)
            existing.created_by = user.get("email", "")
        else:
            sm = SchemaMapping(name=save_as, upload_type=upload_type,
                               created_by=user.get("email", ""))
            sm.set_mapping(mapping)
            db.session.add(sm)
        try:
            db.session.commit()
        except Exception as e:
            log.warning("Could not save schema mapping: %s", e)
            db.session.rollback()

    # Remap rows using confirmed mapping
    rows = _remap_rows(rows, mapping)

    log.info("Mapped upload: type=%s rows=%d file=%s user=%s",
             upload_type, len(rows), orig_filename, user.get("email", ""))
    result = _import_rows(upload_type, rows, user.get("email", ""))
    if result.get("success"):
        log.info("Mapped upload complete: type=%s imported=%d skipped=%d",
                 upload_type, result.get("imported", 0), result.get("skipped", 0))
    return jsonify(result)


def _remap_rows(rows, mapping):
    """Rename row keys according to a mapping dict.

    mapping: {file_header: internal_field_or_None}
    Headers mapped to None are kept as-is (they'll just be ignored
    by the import functions).
    """
    remapped = []
    for row in rows:
        new_row = {}
        for k, v in row.items():
            field = mapping.get(k.strip())
            if field:
                new_row[field] = v
            else:
                new_row[k] = v  # keep unmapped columns in case needed
        remapped.append(new_row)
    return remapped


def _apply_schema_mapping(rows, schema_id):
    """Apply a saved schema mapping to rows."""
    from app.models import SchemaMapping
    sm = SchemaMapping.query.get(schema_id)
    if not sm:
        return rows
    mapping = sm.get_mapping()
    if not mapping:
        return rows
    return _remap_rows(rows, mapping)


# ── Schema profile management ──────────────────────────────────

@data_import_bp.route("/schemas", methods=["GET"])
@admin_required
def list_schemas():
    """List saved schema mapping profiles."""
    from app.models import SchemaMapping
    upload_type = request.args.get("upload_type")
    q = SchemaMapping.query
    if upload_type:
        q = q.filter_by(upload_type=upload_type)
    schemas = q.order_by(SchemaMapping.updated_at.desc()).all()
    return jsonify({
        "schemas": [
            {
                "id": s.id,
                "name": s.name,
                "upload_type": s.upload_type,
                "mapping": s.get_mapping(),
                "created_by": s.created_by,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in schemas
        ]
    })


@data_import_bp.route("/schemas/<int:schema_id>", methods=["DELETE"])
@admin_required
def delete_schema(schema_id):
    """Delete a saved schema mapping profile."""
    dg = _demo_guard()
    if dg:
        return dg
    from app.models import db, SchemaMapping
    sm = SchemaMapping.query.get(schema_id)
    if not sm:
        return jsonify({"success": False, "error": "Schema not found"}), 404
    db.session.delete(sm)
    db.session.commit()
    return jsonify({"success": True})


def _find_col(headers, target):
    """Case-insensitive column match."""
    target_l = target.lower().replace("_", " ")
    for h in headers:
        if h.lower().replace("_", " ") == target_l:
            return h
    return None


def _get_val(row, target):
    """Get a value from a row dict, case-insensitive. Normalises Excel types."""
    for k, v in row.items():
        if k.strip().lower().replace("_", " ") == target.lower().replace("_", " "):
            if v is None or v == "":
                return ""
            # Excel datetime cells → ISO string
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d %H:%M:%S")
            # Excel numeric cells → drop trailing .0 for IDs
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return str(v).strip()
    return ""


def _parse_csv(file_obj):
    """Parse a CSV upload into a list of dicts."""
    text = file_obj.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _parse_excel(file_obj):
    """Parse an Excel upload into a list of dicts."""
    import openpyxl
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(c or "").strip() for c in next(rows_iter)]
    result = []
    for row in rows_iter:
        d = {}
        for i, val in enumerate(row):
            if i < len(headers) and headers[i]:
                d[headers[i]] = val if val is not None else ""
        if any(str(v).strip() for v in d.values()):
            result.append(d)
    return result


def _import_rows(upload_type, rows, uploaded_by=""):
    """Import parsed rows into the database. Returns result dict."""
    from app.models import db, DataUpload, Employee, PlanningUnit
    from app.models import Accommodation, PTOEntry
    from app.models import ForecastInterval, RequirementInterval

    upload = DataUpload(
        filename=f"upload_{upload_type}",
        upload_type=upload_type,
        rows_total=len(rows),
        status="processing",
        uploaded_by=uploaded_by,
    )
    db.session.add(upload)
    db.session.flush()

    imported = 0
    skipped = 0
    errors = []

    try:
        if upload_type == "employees":
            imported, skipped, errors = _import_employees(rows)
        elif upload_type == "forecast":
            imported, skipped, errors = _import_forecast(rows)
        elif upload_type == "requirements":
            imported, skipped, errors = _import_requirements(rows)
        elif upload_type == "accommodations":
            imported, skipped, errors = _import_accommodations(rows)
        elif upload_type == "pto":
            imported, skipped, errors = _import_pto(rows)
        elif upload_type == "actuals":
            imported, skipped, errors = _import_actuals(rows)
        elif upload_type == "agent_status":
            imported, skipped, errors = _import_agent_status(rows)
        elif upload_type == "activities":
            imported, skipped, errors = _import_activities(rows)
        elif upload_type == "contracts":
            imported, skipped, errors = _import_contracts(rows)
        elif upload_type == "planning_units":
            imported, skipped, errors = _import_planning_units(rows)

        upload.rows_imported = imported
        upload.rows_skipped = skipped
        upload.status = "complete"
        if errors:
            upload.error_detail = "\n".join(errors[:20])
        db.session.commit()

        return {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "total": len(rows),
            "errors": errors[:10],
        }
    except Exception as e:
        db.session.rollback()
        # Re-create the upload record since rollback detached it
        try:
            err_upload = DataUpload(
                upload_type=upload.upload_type,
                filename=upload.filename,
                uploaded_by=upload.uploaded_by,
                rows_imported=0,
                rows_skipped=0,
                status="error",
                error_detail=str(e)[:500],
            )
            db.session.add(err_upload)
            db.session.commit()
        except Exception:
            pass  # Best effort — don't mask the original error
        return {"success": False, "error": str(e)}


def _get_or_create_unit(name):
    """Get or create a PlanningUnit by name (LOB names are normalized)."""
    from app.models import db, PlanningUnit
    from app.data_source import normalize_lob
    name = normalize_lob(name.strip())
    if not name:
        return None
    unit = PlanningUnit.query.filter_by(name=name).first()
    if not unit:
        unit = PlanningUnit(name=name)
        db.session.add(unit)
        db.session.flush()
    return unit


def _import_employees(rows):
    from app.models import db, Employee
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        emp_id = _get_val(row, "Employee ID")
        first  = _get_val(row, "First Name")
        last   = _get_val(row, "Last Name")
        if not emp_id or not first:
            skipped += 1
            errors.append(f"Row {i}: missing Employee ID or First Name")
            continue

        # LOB resolution: prefer "Planning Unit" (organizational unit),
        # fall back to "LOB" or "Latest Skill Name" for queue assignment
        lob = (_get_val(row, "Planning Unit")
               or _get_val(row, "LOB")
               or _get_val(row, "Latest Skill Name"))
        unit = _get_or_create_unit(lob) if lob else None

        existing = Employee.query.filter_by(employee_id=emp_id).first()
        emp = existing or Employee(employee_id=emp_id)
        emp.first_name = first
        emp.last_name = last
        status_val = _get_val(row, "Status")
        if status_val:
            emp.status = status_val
        elif not existing:
            emp.status = "Active"
        if unit:
            emp.planning_unit_id = unit.id

        # Skills
        skills_val = _get_val(row, "All Skills")
        if skills_val:
            emp.all_skills = skills_val

        # Email — prefer "Address Email" (HR system), fall back to "Email"
        email_val = _get_val(row, "Address Email") or _get_val(row, "Email")
        if email_val:
            emp.email = email_val

        # Contract type (HR system column)
        ct_val = _get_val(row, "Contract Type")
        if ct_val:
            emp.contract_type = ct_val

        # External IDs
        ext1 = _get_val(row, "External ID 1")
        if ext1:
            emp.external_id_1 = str(ext1).strip()
        ext2 = _get_val(row, "External ID 2")
        if ext2:
            emp.external_id_2 = str(ext2).strip()

        # Languages
        lang = _get_val(row, "Languages")
        if lang:
            emp.languages = str(lang).strip()

        # Contract link (by name)
        contract_name = _get_val(row, "Contract")
        if contract_name:
            from app.models import Contract as ContractModel
            c = ContractModel.query.filter_by(name=str(contract_name).strip()).first()
            if c:
                emp.contract_id = c.id

        # Numeric fields
        wh = _get_val(row, "Weekly Hours")
        if wh:
            try: emp.weekly_hours = float(wh)
            except (ValueError, TypeError): pass
        dpw = _get_val(row, "Days Per Week")
        if dpw:
            try: emp.days_per_week = int(dpw)
            except (ValueError, TypeError): pass
        hpd = _get_val(row, "Hours Per Day")
        if hpd:
            try: emp.hours_per_day = float(hpd)
            except (ValueError, TypeError): pass

        # Timezone
        tz = _get_val(row, "Timezone")
        if tz:
            emp.timezone = str(tz).strip()

        # Team lead
        tl = _get_val(row, "Team Lead")
        if tl:
            emp.team_lead = str(tl).strip()

        # Schedule excluded
        se = _get_val(row, "Schedule Excluded")
        if se:
            emp.schedule_excluded = str(se).strip().lower() in ("true", "yes", "1", "y")

        # Parse dates
        for field, attr in [
            ("Skill Start", "skill_start"),
            ("Latest Skill Start", "skill_start"),
            ("Skill End", "skill_end"),
            ("Latest Skill End", "skill_end"),
            ("End Date", "end_date"),
        ]:
            val = _get_val(row, field)
            if val and val != "4000-01-01":
                ts = _parse_ts(val)
                if ts:
                    setattr(emp, attr, ts.date())

        if not existing:
            db.session.add(emp)
        imported += 1

    db.session.flush()
    return imported, skipped, errors


def _import_forecast(rows):
    """Forecast intervals. Upserts on (LOB, Timestamp).
    Supports either a combined Timestamp column or separate Date + Time columns.
    """
    from app.models import db, ForecastInterval
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        lob = _get_val(row, "LOB")
        if not lob:
            skipped += 1
            continue
        ts = _resolve_timestamp(row)
        if not ts:
            skipped += 1
            errors.append(f"Row {i}: bad timestamp")
            continue
        unit = _get_or_create_unit(lob)
        rec = ForecastInterval.query.filter_by(planning_unit_id=unit.id, timestamp=ts).first()
        if not rec:
            rec = ForecastInterval(planning_unit_id=unit.id, timestamp=ts, source="upload")
            db.session.add(rec)
        rec.offered = _num(_get_val(row, "Offered"))
        rec.aht = _num(_get_val(row, "AHT"))
        rec.source = "upload"
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _import_requirements(rows):
    """Requirement intervals. Upserts on (LOB, Timestamp)."""
    from app.models import db, RequirementInterval
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        ts_str = _get_val(row, "Timestamp")
        lob    = _get_val(row, "LOB")
        if not ts_str or not lob:
            skipped += 1
            continue
        unit = _get_or_create_unit(lob)
        ts = _parse_ts(ts_str)
        if not ts:
            skipped += 1
            errors.append(f"Row {i}: bad timestamp '{ts_str}'")
            continue
        rec = RequirementInterval.query.filter_by(planning_unit_id=unit.id, timestamp=ts).first()
        if not rec:
            rec = RequirementInterval(planning_unit_id=unit.id, timestamp=ts, source="upload")
            db.session.add(rec)
        rec.agents_required = _num(_get_val(row, "Agents Required"))
        rec.source = "upload"
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _import_accommodations(rows):
    from app.models import db, Accommodation, Employee
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        emp_name = _get_val(row, "Employee")
        if not emp_name:
            skipped += 1
            continue
        parts = emp_name.split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        emp = Employee.query.filter_by(first_name=first, last_name=last).first()
        if not emp:
            skipped += 1
            errors.append(f"Row {i}: employee '{emp_name}' not found in database")
            continue

        # Update or create
        accom = Accommodation.query.filter_by(employee_id=emp.id).first()
        if not accom:
            accom = Accommodation(employee_id=emp.id)
            db.session.add(accom)
        for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            val = _get_val(row, day)
            if val:
                setattr(accom, day.lower(), val)
        ss = _get_val(row, "Shift Start")
        if ss:
            accom.shift_start = ss
        se = _get_val(row, "Shift End")
        if se:
            accom.shift_end = se
        notes = _get_val(row, "Notes")
        if notes:
            accom.notes = notes
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _import_pto(rows):
    from app.models import db, PTOEntry, Employee
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        emp_name   = _get_val(row, "Employee")
        start_str  = _get_val(row, "Start Date")
        end_str    = _get_val(row, "End Date")
        if not emp_name or not start_str or not end_str:
            skipped += 1
            continue
        parts = emp_name.split(None, 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""
        emp = Employee.query.filter_by(first_name=first, last_name=last).first()
        if not emp:
            skipped += 1
            errors.append(f"Row {i}: employee '{emp_name}' not found")
            continue
        try:
            sd = datetime.strptime(start_str[:10], "%Y-%m-%d").date()
            ed = datetime.strptime(end_str[:10], "%Y-%m-%d").date()
        except ValueError:
            skipped += 1
            errors.append(f"Row {i}: bad date format")
            continue
        entry = PTOEntry(
            employee_id=emp.id,
            start_date=sd,
            end_date=ed,
            pto_type=_get_val(row, "Type") or "full",
            note=_get_val(row, "Note"),
        )
        db.session.add(entry)
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _parse_ts(ts_str):
    """Parse 'YYYY-MM-DD HH:MM[:SS]' or 'YYYY-MM-DD'; returns datetime or None."""
    ts_str = str(ts_str or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(ts_str[:19], fmt)
        except ValueError:
            continue
    return None


def _resolve_timestamp(row):
    """Resolve a timestamp from either a single Timestamp column or separate Date + Time columns.

    Handles:
      - Combined Timestamp column: "2023-01-01 09:00" (a full datetime)
      - Separate Date + Time columns: Date="2023-01-01", Time/Timestamp="09:00"
      - Excel datetime/time objects in either column
    Returns datetime or None.
    """
    import datetime as dt_mod

    # Grab raw values (before string conversion) to detect Excel types
    ts_raw_obj = row.get("Timestamp") or row.get("timestamp") or None
    date_raw_obj = row.get("Date") or row.get("date") or None
    time_raw_obj = row.get("Time") or row.get("time") or None

    # If Timestamp is a full datetime (not just a time), use it directly
    if isinstance(ts_raw_obj, dt_mod.datetime):
        return ts_raw_obj
    if isinstance(ts_raw_obj, str) and ts_raw_obj.strip():
        ts = _parse_ts(ts_raw_obj)
        if ts and ts.hour + ts.minute > 0:
            return ts
        # If it parsed to midnight with no time component, it might be just a date
        if ts and date_raw_obj is None:
            return ts

    # We have separate Date + Time (or Timestamp is just a time value)
    # Resolve the date part
    date_part = None
    if isinstance(date_raw_obj, dt_mod.datetime):
        date_part = date_raw_obj.date()
    elif isinstance(date_raw_obj, dt_mod.date):
        date_part = date_raw_obj
    elif date_raw_obj:
        date_str = str(date_raw_obj).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                date_part = dt_mod.datetime.strptime(date_str[:19], fmt).date()
                break
            except ValueError:
                continue

    if not date_part:
        return None

    # Resolve the time part — check Time column first, then Timestamp column
    time_part = None
    for t_obj in (time_raw_obj, ts_raw_obj):
        if t_obj is None:
            continue
        if isinstance(t_obj, dt_mod.time):
            time_part = t_obj
            break
        if isinstance(t_obj, dt_mod.datetime):
            time_part = t_obj.time()
            break
        t_str = str(t_obj).strip()
        if not t_str:
            continue
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"):
            try:
                time_part = dt_mod.datetime.strptime(t_str, fmt).time()
                break
            except ValueError:
                continue
        if time_part:
            break

    if not time_part:
        time_part = dt_mod.time(0, 0)

    return dt_mod.datetime.combine(date_part, time_part)


def _num(v, default=0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _import_actuals(rows):
    """Interval call actuals. Upserts on (LOB, Timestamp).
    Supports either a combined Timestamp column or separate Date + Time columns.
    """
    from app.models import db, IntervalActual
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        lob = _get_val(row, "LOB")
        ts = _resolve_timestamp(row)
        if not ts or not lob:
            skipped += 1
            if lob and not ts:
                errors.append(f"Row {i}: bad timestamp")
            continue
        unit = _get_or_create_unit(lob)
        rec = IntervalActual.query.filter_by(planning_unit_id=unit.id, timestamp=ts).first()
        if not rec:
            rec = IntervalActual(planning_unit_id=unit.id, timestamp=ts)
            db.session.add(rec)
        rec.offered         = int(_num(_get_val(row, "Offered")))
        rec.answered        = int(_num(_get_val(row, "Answered")))
        aw = _get_val(row, "Answered Within")
        rec.answered_within = int(_num(aw)) if aw not in (None, "") else rec.answered
        rec.abandoned       = int(_num(_get_val(row, "Abandoned")))
        rec.rolled          = int(_num(_get_val(row, "Rolled")))
        asa = _get_val(row, "ASA"); aht = _get_val(row, "AHT"); mq = _get_val(row, "Max Queued")
        rec.asa_secs   = _num(asa) if asa not in (None, "") else None
        rec.aht_secs   = _num(aht) if aht not in (None, "") else None
        rec.max_queued = int(_num(mq)) if mq not in (None, "") else None
        rec.source = "upload"
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _import_agent_status(rows):
    """Agent status periods keyed by external Employee ID."""
    from app.models import db, Employee, AgentStatusEvent
    imported, skipped = 0, 0
    errors = []
    emp_cache = {}
    for i, row in enumerate(rows, start=2):
        ext_id = str(_get_val(row, "Employee ID") or "").strip()
        status = str(_get_val(row, "Status") or "").strip()
        start = _parse_ts(_get_val(row, "Start"))
        end = _parse_ts(_get_val(row, "End"))
        if not ext_id or not status or not start:
            skipped += 1
            continue
        if ext_id not in emp_cache:
            emp_cache[ext_id] = Employee.query.filter_by(employee_id=ext_id).first()
        emp = emp_cache[ext_id]
        if not emp:
            skipped += 1
            errors.append(f"Row {i}: unknown employee '{ext_id}'")
            continue
        existing = AgentStatusEvent.query.filter_by(employee_id=emp.id, start_ts=start).first()
        if existing:
            existing.status = status; existing.end_ts = end
        else:
            db.session.add(AgentStatusEvent(employee_id=emp.id, status=status,
                                            start_ts=start, end_ts=end, source="upload"))
        imported += 1
    db.session.flush()
    return imported, skipped, errors


# ═══════════════════════════════════════════════════════════════
# ACTIVITIES / SEGMENT CODES IMPORT
# ═══════════════════════════════════════════════════════════════

def _bool_val(val):
    """Convert a cell value to boolean. None/empty → None (skip)."""
    if val is None or str(val).strip() == "":
        return None
    return str(val).strip().lower() in ("true", "yes", "1", "y")


def _int_val(val):
    """Convert a cell value to int, or None."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _import_activities(rows):
    """Import or update SegmentCode activities. Upserts on code."""
    from app.models import db, SegmentCode
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        code = str(_get_val(row, "Code") or "").strip()
        label = str(_get_val(row, "Label") or "").strip()
        if not code or not label:
            skipped += 1
            errors.append(f"Row {i}: missing Code or Label")
            continue

        existing = SegmentCode.query.filter_by(code=code).first()
        sc = existing or SegmentCode(code=code)
        sc.label = label

        # Simple string fields
        for csv_col, attr in [
            ("Color", "color"), ("Activity Type", "activity_type"),
            ("Activity Category", "activity_category"),
            ("Official Name", "official_name"), ("Abbreviation", "abbreviation"),
            ("Shortcut", "shortcut"), ("External IDs", "external_ids"),
        ]:
            v = _get_val(row, csv_col)
            if v:
                setattr(sc, attr, str(v).strip())

        # Parent code (resolve to id)
        parent_code = _get_val(row, "Parent Code")
        if parent_code:
            parent = SegmentCode.query.filter_by(code=str(parent_code).strip()).first()
            if parent:
                sc.parent_id = parent.id

        # Boolean fields
        for csv_col, attr in [
            ("Is Productive", "is_productive"), ("Is Paid", "is_paid"),
            ("Is Multi-Activity", "is_multi_activity"),
            ("Is Replaceable", "is_replaceable"), ("Is Plannable", "is_plannable"),
            ("Is Requestable", "is_requestable"), ("Is Exchangeable", "is_exchangeable"),
            ("Allow Full Day", "allow_full_day"),
            ("Can Be Day Status", "can_be_day_status"), ("Is Flexible", "is_flexible"),
        ]:
            bv = _bool_val(_get_val(row, csv_col))
            if bv is not None:
                setattr(sc, attr, bv)

        # Integer fields
        for csv_col, attr in [
            ("Importance", "importance"), ("Priority", "priority"),
            ("Offset Mins", "offset_mins"), ("Duration Mins", "duration_mins"),
            ("Window Start Mins", "window_start_mins"),
            ("Window End Mins", "window_end_mins"),
        ]:
            iv = _int_val(_get_val(row, csv_col))
            if iv is not None:
                setattr(sc, attr, iv)

        if not existing:
            db.session.add(sc)
        imported += 1

    db.session.flush()
    return imported, skipped, errors


# ═══════════════════════════════════════════════════════════════
# CONTRACTS IMPORT
# ═══════════════════════════════════════════════════════════════

def _import_contracts(rows):
    """Import or update Contract templates. Upserts on name."""
    from app.models import db, Contract
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        name = str(_get_val(row, "Name") or "").strip()
        if not name:
            skipped += 1
            errors.append(f"Row {i}: missing Name")
            continue

        existing = Contract.query.filter_by(name=name).first()
        c = existing or Contract(name=name)

        # String fields
        for csv_col, attr in [
            ("Abbreviation", "abbreviation"), ("Color", "color"),
            ("Contract Type", "contract_type"),
            ("Workdays Calculation", "workdays_calculation"),
        ]:
            v = _get_val(row, csv_col)
            if v:
                setattr(c, attr, str(v).strip())

        # Float fields
        for csv_col, attr in [
            ("Daily Hours Min", "daily_hours_min"),
            ("Daily Hours Target", "daily_hours_target"),
            ("Daily Hours Max", "daily_hours_max"),
            ("Weekly Hours Min", "weekly_hours_min"),
            ("Weekly Hours Target", "weekly_hours_target"),
            ("Weekly Hours Max", "weekly_hours"),
            ("Monthly Hours Max", "monthly_hours_max"),
            ("Min Rest Hours", "min_rest_hours"),
            ("Break After Hours", "break_after_hours"),
        ]:
            v = _get_val(row, csv_col)
            if v:
                try: setattr(c, attr, float(v))
                except (ValueError, TypeError): pass

        # Integer fields
        for csv_col, attr in [
            ("Days Per Week", "days_per_week"),
            ("Break Duration Mins", "break_duration_mins"),
            ("Lunch Duration Mins", "lunch_duration_mins"),
            ("Max Consecutive Days", "max_consecutive_days"),
            ("Min Days Per Week", "min_days_per_week"),
            ("Max Days Per Week", "max_days_per_week"),
            ("Min Days Off Per Week", "min_days_off_per_week"),
            ("Max Night Shifts Week", "max_night_shifts_week"),
            ("Max Night Shifts Month", "max_night_shifts_month"),
        ]:
            iv = _int_val(_get_val(row, csv_col))
            if iv is not None:
                setattr(c, attr, iv)

        # Boolean fields
        for csv_col, attr in [
            ("Overtime Eligible", "overtime_eligible"),
        ]:
            bv = _bool_val(_get_val(row, csv_col))
            if bv is not None:
                setattr(c, attr, bv)

        if not existing:
            db.session.add(c)
        imported += 1

    db.session.flush()
    return imported, skipped, errors


# ═══════════════════════════════════════════════════════════════
# PLANNING UNITS IMPORT
# ═══════════════════════════════════════════════════════════════

def _import_planning_units(rows):
    """Import or update Planning Units with optional business hours. Upserts on name."""
    from app.models import db, PlanningUnit, PlanningUnitBusinessHours
    imported, skipped = 0, 0
    errors = []
    day_pairs = [
        ("Mon", "monday"), ("Tue", "tuesday"), ("Wed", "wednesday"),
        ("Thu", "thursday"), ("Fri", "friday"), ("Sat", "saturday"), ("Sun", "sunday"),
    ]
    for i, row in enumerate(rows, start=2):
        name = str(_get_val(row, "Name") or "").strip()
        if not name:
            skipped += 1
            errors.append(f"Row {i}: missing Name")
            continue

        existing = PlanningUnit.query.filter_by(name=name).first()
        pu = existing or PlanningUnit(name=name)

        desc = _get_val(row, "Description")
        if desc:
            pu.description = str(desc).strip()
        tz = _get_val(row, "Timezone")
        if tz:
            pu.timezone = str(tz).strip()
        active = _bool_val(_get_val(row, "Is Active"))
        if active is not None:
            pu.is_active = active

        if not existing:
            db.session.add(pu)
            db.session.flush()  # need pu.id for business hours

        # Business hours per day (e.g. "Mon Open"="08:00", "Mon Close"="22:00")
        for prefix, day_type in day_pairs:
            open_val = _get_val(row, f"{prefix} Open")
            close_val = _get_val(row, f"{prefix} Close")
            if open_val and close_val:
                open_str = str(open_val).strip()
                close_str = str(close_val).strip()
                # Upsert: find existing row for this day type
                bh = PlanningUnitBusinessHours.query.filter_by(
                    planning_unit_id=pu.id, day_type=day_type
                ).first()
                if bh:
                    bh.open_time = open_str
                    bh.close_time = close_str
                else:
                    db.session.add(PlanningUnitBusinessHours(
                        planning_unit_id=pu.id, day_type=day_type,
                        open_time=open_str, close_time=close_str,
                    ))

        imported += 1

    db.session.flush()
    return imported, skipped, errors


# ═══════════════════════════════════════════════════════════════
# GOOGLE SHEET SYNC (employee headcount)
# ═══════════════════════════════════════════════════════════════

def _open_employee_sheet():
    """
    Open the employee headcount Google Sheet (external HR export).
    Uses EMPLOYEE_SHEET_KEY env var, falling back to AppSetting.
    Returns (worksheet, error_string).
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]

        sheet_key = os.environ.get("EMPLOYEE_SHEET_KEY", "")
        sa_file = os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json")
        sa_json = ""

        # Fall back to database settings
        if not sheet_key:
            try:
                from app.models import AppSetting
                sheet_key = AppSetting.get("employee_sheet_key", "")
            except Exception:
                log.warning("Failed to read employee_sheet_key from DB settings", exc_info=True)

        if not sheet_key:
            return None, "No employee sheet key configured. Set EMPLOYEE_SHEET_KEY in environment or in Settings → Connections."

        # Try JSON creds from DB first, then file
        try:
            from app.models import AppSetting
            sa_json = AppSetting.get("google_service_account_json", "")
        except Exception:
            log.warning("Failed to read google_service_account_json from DB settings", exc_info=True)

        if sa_json:
            import json
            creds_dict = json.loads(sa_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        elif sa_file and os.path.exists(sa_file):
            creds = ServiceAccountCredentials.from_json_keyfile_name(sa_file, scope)
        else:
            return None, "No service account credentials configured"

        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_key)
        # Try to find the EMPLOYEES tab, fall back to first sheet
        try:
            ws = spreadsheet.worksheet("EMPLOYEES")
        except Exception:
            ws = spreadsheet.sheet1
        return ws, None
    except Exception as e:
        return None, f"Could not open employee sheet: {e}"


@data_import_bp.route("/sync-employees", methods=["POST"])
@admin_required
def sync_employees_from_sheet():
    """
    Pull employees from the connected HR Google Sheet and
    upsert them into the database.
    """
    dg = _demo_guard()
    if dg:
        return dg

    user = get_current_user()
    active_only = request.json.get("active_only", True) if request.is_json else True

    ws, err = _open_employee_sheet()
    if err:
        return jsonify({"success": False, "error": err})

    try:
        records = ws.get_all_records()
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not read sheet: {e}"})

    if not records:
        return jsonify({"success": False, "error": "Sheet is empty"})

    # Filter to active employees if requested
    if active_only:
        records = [r for r in records if str(r.get("Status", "")).strip() in ("Active", "LOA")]

    log.info("Sheet sync started: %d rows (active_only=%s) by %s",
             len(records), active_only, user.get("email", ""))

    # Convert sheet records to the format _import_employees expects
    rows = []
    for rec in records:
        row = {}
        for k, v in rec.items():
            row[str(k).strip()] = str(v).strip() if v else ""
        rows.append(row)

    from app.models import db, DataUpload
    upload = DataUpload(
        filename="hr_sheet_sync",
        upload_type="employees",
        rows_total=len(rows),
        status="processing",
        uploaded_by=user.get("email", ""),
    )
    db.session.add(upload)
    db.session.flush()

    try:
        imported, skipped, errors = _import_employees(rows)
        upload.rows_imported = imported
        upload.rows_skipped = skipped
        upload.status = "complete"
        if errors:
            upload.error_detail = "\n".join(errors[:20])
        db.session.commit()

        log.info("Sheet sync complete: imported=%d skipped=%d", imported, skipped)
        return jsonify({
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "total": len(rows),
            "errors": errors[:10],
            "source": "employee_sheet_sync",
        })
    except Exception as e:
        db.session.rollback()
        log.error("Sheet sync failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@data_import_bp.route("/sync-employees/preview", methods=["GET"])
@admin_required
def preview_sheet_sync():
    """
    Preview what the sheet sync would import — returns summary stats
    without writing anything.
    """
    ws, err = _open_employee_sheet()
    if err:
        return jsonify({"connected": False, "error": err})

    try:
        records = ws.get_all_records()
    except Exception as e:
        return jsonify({"connected": False, "error": f"Could not read sheet: {e}"})

    from collections import Counter
    statuses = Counter(str(r.get("Status", "")).strip() for r in records)
    planning_units = Counter(str(r.get("Planning Unit", "")).strip()
                             for r in records
                             if str(r.get("Status", "")).strip() == "Active")

    # Check how many already exist in DB
    from app.models import Employee
    existing_ids = {e.employee_id for e in Employee.query.with_entities(Employee.employee_id).all()}
    sheet_ids = {str(r.get("Employee ID", "")).strip() for r in records}
    new_count = len(sheet_ids - existing_ids)
    update_count = len(sheet_ids & existing_ids)

    return jsonify({
        "connected": True,
        "total_rows": len(records),
        "statuses": dict(statuses),
        "active_planning_units": dict(planning_units),
        "new_employees": new_count,
        "existing_employees": update_count,
    })
