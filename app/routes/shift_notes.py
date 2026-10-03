"""
Shift Notes — supervisor handoff notes between shifts.
"""
import logging
from datetime import date, timedelta, datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import db, ShiftNote, User

log = logging.getLogger(__name__)
shift_notes_bp = Blueprint("shift_notes", __name__, url_prefix="/shift-notes")


def _utcnow():
    return datetime.now(timezone.utc)


@shift_notes_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return "Forbidden", 403
    return render_template("shift_notes/index.html", user=user)


@shift_notes_bp.route("/api/list", methods=["POST"])
@login_required
def list_notes():
    """Return shift notes for a date range."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_shift_notes
        return jsonify(success=True, notes=get_demo_shift_notes())

    data = request.get_json(silent=True) or {}
    try:
        start = date.fromisoformat(data.get("start_date", ""))
        end = date.fromisoformat(data.get("end_date", ""))
    except (ValueError, TypeError):
        end = date.today()
        start = end - timedelta(days=6)

    notes = (
        ShiftNote.query
        .filter(ShiftNote.note_date >= start, ShiftNote.note_date <= end)
        .order_by(ShiftNote.note_date.desc(), ShiftNote.created_at.desc())
        .limit(100)
        .all()
    )
    rows = []
    for n in notes:
        author = User.query.get(n.created_by)
        rows.append({
            "id": n.id,
            "note_date": n.note_date.isoformat(),
            "shift_label": n.shift_label or "",
            "body": n.body or "",
            "category": n.category,
            "is_resolved": n.is_resolved,
            "author": author.display_name if author else "Unknown",
            "created_at": n.created_at.isoformat() if n.created_at else "",
        })
    return jsonify(success=True, notes=rows)


@shift_notes_bp.route("/api/save", methods=["POST"])
@login_required
def save_note():
    """Create or update a shift note."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify(success=False, error="Note body is required")

    note_id = data.get("id")
    if note_id:
        note = ShiftNote.query.get(note_id)
        if not note:
            return jsonify(success=False, error="Not found")
    else:
        note = ShiftNote(created_by=user["id"])
        db.session.add(note)

    try:
        note.note_date = date.fromisoformat(data.get("note_date", date.today().isoformat()))
    except (ValueError, TypeError):
        note.note_date = date.today()

    note.body = body
    note.shift_label = (data.get("shift_label") or "").strip()
    note.category = data.get("category", "general")
    note.is_resolved = bool(data.get("is_resolved", False))

    db.session.commit()
    return jsonify(success=True, id=note.id)


@shift_notes_bp.route("/api/resolve", methods=["POST"])
@login_required
def resolve_note():
    """Toggle resolved status."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    note = ShiftNote.query.get(data.get("id"))
    if not note:
        return jsonify(success=False, error="Not found")

    note.is_resolved = not note.is_resolved
    db.session.commit()
    return jsonify(success=True, is_resolved=note.is_resolved)


@shift_notes_bp.route("/api/delete", methods=["POST"])
@login_required
def delete_note():
    """Delete a shift note."""
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        return jsonify(error="Forbidden"), 403

    data = request.get_json(silent=True) or {}
    note = ShiftNote.query.get(data.get("id"))
    if not note:
        return jsonify(success=False, error="Not found")

    db.session.delete(note)
    db.session.commit()
    return jsonify(success=True)
