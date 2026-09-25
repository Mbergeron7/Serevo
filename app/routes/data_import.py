"""
routes/data_import.py — CSV / Excel bulk upload
=================================================
Allows admins to upload CSV or Excel files to populate the database
tables (employees, forecast, requirements, accommodations, PTO).
"""

import io
import csv
import logging
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect,
                   url_for, jsonify, flash)
from app.auth import login_required, admin_required, get_current_user
from config import cfg

log = logging.getLogger("serevo.data_import")

data_import_bp = Blueprint("data_import", __name__, url_prefix="/data")


# ── Column mappings per upload type ──────────────────────────
UPLOAD_TYPES = {
    "employees": {
        "label": "Employees",
        "required": ["Employee ID", "First Name", "Last Name"],
        "optional": ["Status", "LOB", "All Skills", "Skill Start", "Skill End", "End Date"],
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
    user = get_current_user()
    upload_type = request.form.get("upload_type", "")
    if upload_type not in UPLOAD_TYPES:
        return jsonify({"success": False, "error": f"Unknown upload type: {upload_type}"})

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file selected"})

    filename = file.filename.lower()
    try:
        if filename.endswith((".xlsx", ".xls")):
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
    result = _import_rows(upload_type, rows, user.get("email", ""))
    return jsonify(result)


def _find_col(headers, target):
    """Case-insensitive column match."""
    target_l = target.lower().replace("_", " ")
    for h in headers:
        if h.lower().replace("_", " ") == target_l:
            return h
    return None


def _get_val(row, target):
    """Get a value from a row dict, case-insensitive."""
    for k, v in row.items():
        if k.strip().lower().replace("_", " ") == target.lower().replace("_", " "):
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
        upload.status = "error"
        upload.error_detail = str(e)
        db.session.add(upload)
        db.session.commit()
        return {"success": False, "error": str(e)}


def _get_or_create_unit(name):
    """Get or create a PlanningUnit by name."""
    from app.models import db, PlanningUnit
    name = name.strip()
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

        lob = _get_val(row, "LOB") or _get_val(row, "Latest Skill Name")
        unit = _get_or_create_unit(lob) if lob else None

        existing = Employee.query.filter_by(employee_id=emp_id).first()
        if existing:
            existing.first_name = first
            existing.last_name = last
            existing.status = _get_val(row, "Status") or "Active"
            existing.planning_unit_id = unit.id if unit else existing.planning_unit_id
            existing.all_skills = _get_val(row, "All Skills") or existing.all_skills
        else:
            emp = Employee(
                employee_id=emp_id,
                first_name=first,
                last_name=last,
                status=_get_val(row, "Status") or "Active",
                planning_unit_id=unit.id if unit else None,
                all_skills=_get_val(row, "All Skills"),
            )
            # Parse dates
            for field, attr in [("Skill Start", "skill_start"), ("Skill End", "skill_end"), ("End Date", "end_date")]:
                val = _get_val(row, field)
                if val:
                    try:
                        setattr(emp, attr, datetime.strptime(val[:10], "%Y-%m-%d").date())
                    except ValueError:
                        pass
            db.session.add(emp)
        imported += 1

    db.session.flush()
    return imported, skipped, errors


def _import_forecast(rows):
    from app.models import db, ForecastInterval
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        ts_str  = _get_val(row, "Timestamp")
        lob     = _get_val(row, "LOB")
        offered = _get_val(row, "Offered")
        aht     = _get_val(row, "AHT")
        if not ts_str or not lob:
            skipped += 1
            continue
        unit = _get_or_create_unit(lob)
        try:
            ts = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                ts = datetime.strptime(ts_str[:10], "%Y-%m-%d")
            except ValueError:
                skipped += 1
                errors.append(f"Row {i}: bad timestamp '{ts_str}'")
                continue
        fi = ForecastInterval(
            planning_unit_id=unit.id,
            timestamp=ts,
            offered=float(offered or 0),
            aht=float(aht or 0),
            source="upload",
        )
        db.session.add(fi)
        imported += 1
    db.session.flush()
    return imported, skipped, errors


def _import_requirements(rows):
    from app.models import db, RequirementInterval
    imported, skipped = 0, 0
    errors = []
    for i, row in enumerate(rows, start=2):
        ts_str = _get_val(row, "Timestamp")
        lob    = _get_val(row, "LOB")
        agents = _get_val(row, "Agents Required")
        if not ts_str or not lob:
            skipped += 1
            continue
        unit = _get_or_create_unit(lob)
        try:
            ts = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                ts = datetime.strptime(ts_str[:10], "%Y-%m-%d")
            except ValueError:
                skipped += 1
                errors.append(f"Row {i}: bad timestamp '{ts_str}'")
                continue
        ri = RequirementInterval(
            planning_unit_id=unit.id,
            timestamp=ts,
            agents_required=float(agents or 0),
            source="upload",
        )
        db.session.add(ri)
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
        accom.shift_start = _get_val(row, "Shift Start")
        accom.shift_end = _get_val(row, "Shift End")
        accom.notes = _get_val(row, "Notes")
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
