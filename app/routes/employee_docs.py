"""
Employee Documents — upload, list, download, and delete files
attached to employee records.
"""
import logging
from io import BytesIO

from flask import Blueprint, render_template, request, jsonify, send_file
from app.auth import login_required, get_current_user
from app.models import db, Employee, EmployeeDocument, User

log = logging.getLogger(__name__)
employee_docs_bp = Blueprint("employee_docs", __name__, url_prefix="/employee-docs")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv",
    "png", "jpg", "jpeg", "gif",
    "txt", "rtf",
}
ALLOWED_MIME_PREFIXES = (
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats", "application/vnd.ms-excel",
    "text/", "image/png", "image/jpeg", "image/gif",
    "application/octet-stream",
)


def _check_role(user):
    return user and user.get("role") in ("admin", "supervisor")


@employee_docs_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not _check_role(user):
        return "Forbidden", 403
    employees = Employee.query.filter(Employee.status == "Active").order_by(Employee.last_name).all()
    return render_template("employee_docs/index.html", user=user, employees=employees)


@employee_docs_bp.route("/api/list", methods=["POST"])
@login_required
def list_docs():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_employee_docs
        return jsonify(success=True, docs=get_demo_employee_docs())

    data = request.get_json(silent=True) or {}
    emp_id = data.get("employee_id")
    category = data.get("category", "")

    q = EmployeeDocument.query
    if emp_id:
        q = q.filter(EmployeeDocument.employee_id == emp_id)
    if category:
        q = q.filter(EmployeeDocument.category == category)
    q = q.order_by(EmployeeDocument.created_at.desc()).limit(200)

    rows = []
    for d in q.all():
        emp = Employee.query.get(d.employee_id)
        uploader = User.query.get(d.uploaded_by)
        rows.append({
            "id": d.id,
            "employee_id": d.employee_id,
            "employee_name": f"{emp.first_name} {emp.last_name}" if emp else "Unknown",
            "title": d.title,
            "category": d.category,
            "filename": d.filename,
            "mime_type": d.mime_type,
            "file_size": d.file_size,
            "notes": d.notes or "",
            "uploaded_by": uploader.display_name if uploader else "Unknown",
            "created_at": d.created_at.isoformat() if d.created_at else "",
        })
    return jsonify(success=True, docs=rows)


@employee_docs_bp.route("/api/upload", methods=["POST"])
@login_required
def upload_doc():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(success=False, error="No file selected")

    emp_id = request.form.get("employee_id", type=int)
    if not emp_id or not Employee.query.get(emp_id):
        return jsonify(success=False, error="Invalid employee")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(success=False, error=f"File type .{ext} not allowed")

    file_data = file.read()
    if len(file_data) > MAX_FILE_SIZE:
        return jsonify(success=False, error="File exceeds 10 MB limit")

    title = request.form.get("title", "").strip() or file.filename
    category = request.form.get("category", "general")
    notes = request.form.get("notes", "").strip()

    doc = EmployeeDocument(
        employee_id=emp_id,
        title=title,
        category=category,
        filename=file.filename,
        mime_type=file.content_type or "application/octet-stream",
        file_size=len(file_data),
        file_data=file_data,
        notes=notes,
        uploaded_by=user["id"],
    )
    db.session.add(doc)
    db.session.commit()
    return jsonify(success=True, id=doc.id)


@employee_docs_bp.route("/api/download/<int:doc_id>")
@login_required
def download_doc(doc_id):
    user = get_current_user()
    if not _check_role(user):
        return "Forbidden", 403

    doc = EmployeeDocument.query.get(doc_id)
    if not doc:
        return "Not found", 404

    return send_file(
        BytesIO(doc.file_data),
        mimetype=doc.mime_type,
        as_attachment=True,
        download_name=doc.filename,
    )


@employee_docs_bp.route("/api/delete", methods=["POST"])
@login_required
def delete_doc():
    user = get_current_user()
    if not _check_role(user):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    doc = EmployeeDocument.query.get(data.get("id"))
    if not doc:
        return jsonify(success=False, error="Not found")

    db.session.delete(doc)
    db.session.commit()
    return jsonify(success=True)
