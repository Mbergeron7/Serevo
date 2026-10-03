"""
routes/data_import.py — CSV / Excel bulk upload + Google Sheet sync
===================================================================
Allows admins to upload CSV or Excel files to populate the database
tables (employees, forecast, requirements, accommodations, PTO).

Also provides a sync endpoint to import employees from a connected
Google Sheet (e.g. HR system headcount export).
"""

import io
import csv
import os
import logging
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, flash)
from app.auth import login_required, admin_required, get_current_user
from config import cfg

log = logging.getLogger("serevo.data_import")

data_import_bp = Blueprint("data_import", __name__, url_prefix="/data")


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
                      "Address Email", "Start Date", "Title"],
    },
    "forecast": {
        "label": "Forecast Data",
        "required": ["Timestamp", "LOB", "Offered", "AHT"],
        "optional": [],
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
        "required": ["Timestamp", "LOB", "Offered", "Answered"],
        "optional": ["Answered Within", "Abandoned", "Rolled", "ASA", "AHT", "Max Queued"],
    },
    "agent_status": {
        "label": "Agent Status Events",
        "required": ["Employee ID", "Status", "Start"],
        "optional": ["End"],
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

    # Validate required columns
    type_info = UPLOAD_TYPES[upload_type]
    headers = [h.strip() for h in rows[0].keys()]
    missing = [c for c in type_info["required"] if not _find_col(headers, c)]
    if missing:
        return jsonify({
            "success": False,
            "error": f"Missing required columns: {', '.join(missing)}. Found: {', '.join(headers)}"
        })

    # Process rows
    log.info("Upload started: type=%s rows=%d file=%s user=%s", upload_type, len(rows), file.filename, user.get("email", ""))
    result = _import_rows(upload_type, rows, user.get("email", ""))
    if result.get("success"):
        log.info("Upload complete: type=%s imported=%d skipped=%d", upload_type, result.get("imported", 0), result.get("skipped", 0))
    else:
        log.error("Upload failed: type=%s error=%s", upload_type, result.get("error", ""))
    return jsonify(result)


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
    """Forecast intervals. Upserts on (LOB, Timestamp)."""
    from app.models import db, ForecastInterval
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        ts_str  = _get_val(row, "Timestamp")
        lob     = _get_val(row, "LOB")
        if not ts_str or not lob:
            skipped += 1
            continue
        unit = _get_or_create_unit(lob)
        ts = _parse_ts(ts_str)
        if not ts:
            skipped += 1
            errors.append(f"Row {i}: bad timestamp '{ts_str}'")
            continue
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


def _num(v, default=0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _import_actuals(rows):
    """Interval call actuals. Upserts on (LOB, Timestamp)."""
    from app.models import db, IntervalActual
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        ts = _parse_ts(_get_val(row, "Timestamp"))
        lob = _get_val(row, "LOB")
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
