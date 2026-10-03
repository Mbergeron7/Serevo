"""
Support Tickets — all users can submit; admins can manage and respond.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import db, SupportTicket, TicketComment, User

log = logging.getLogger(__name__)
support_bp = Blueprint("support", __name__, url_prefix="/support")


def _utcnow():
    return datetime.now(timezone.utc)


# ── Main page ────────────────────────────────────────────────

@support_bp.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("support/index.html", user=user)


# ── API: list tickets ────────────────────────────────────────

@support_bp.route("/api/tickets", methods=["POST"])
@login_required
def list_tickets():
    user = get_current_user()
    if user and user.get("is_demo"):
        from app.demo_data import get_demo_support_tickets
        return jsonify(success=True, tickets=get_demo_support_tickets())

    data = request.get_json(silent=True) or {}
    status_filter = data.get("status")
    category_filter = data.get("category")

    query = SupportTicket.query

    # Non-admins see only their own tickets
    if user.get("role") != "admin":
        query = query.filter_by(submitted_by=user["id"])

    if status_filter and status_filter != "all":
        query = query.filter_by(status=status_filter)
    if category_filter and category_filter != "all":
        query = query.filter_by(category=category_filter)

    query = query.order_by(SupportTicket.created_at.desc())
    tickets = query.limit(100).all()

    return jsonify(success=True, tickets=[t.to_dict() for t in tickets])


# ── API: create ticket ───────────────────────────────────────

@support_bp.route("/api/tickets/create", methods=["POST"])
@login_required
def create_ticket():
    user = get_current_user()
    data = request.get_json(silent=True) or {}

    subject = (data.get("subject") or "").strip()
    description = (data.get("description") or "").strip()
    category = data.get("category", "general")
    priority = data.get("priority", "medium")

    if not subject or not description:
        return jsonify(success=False, error="Subject and description are required"), 400

    ticket = SupportTicket(
        subject=subject,
        description=description,
        category=category,
        priority=priority,
        submitted_by=user["id"],
    )
    db.session.add(ticket)
    db.session.commit()

    log.info(f"Support ticket #{ticket.id} created by {user.get('email')}")
    return jsonify(success=True, ticket=ticket.to_dict())


# ── API: get single ticket with comments ─────────────────────

@support_bp.route("/api/tickets/<int:ticket_id>", methods=["GET"])
@login_required
def get_ticket(ticket_id):
    user = get_current_user()

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_support_ticket
        data = get_demo_support_ticket(ticket_id)
        return jsonify(success=True, **data)

    ticket = SupportTicket.query.get_or_404(ticket_id)

    # Non-admins can only view their own tickets
    if user.get("role") != "admin" and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    comments = [c.to_dict() for c in ticket.comments]
    return jsonify(success=True, ticket=ticket.to_dict(), comments=comments)


# ── API: update ticket (admin only for status/assignment) ────

@support_bp.route("/api/tickets/update", methods=["POST"])
@login_required
def update_ticket():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    ticket_id = data.get("id")

    if not ticket_id:
        return jsonify(success=False, error="Missing ticket id"), 400

    ticket = SupportTicket.query.get_or_404(ticket_id)

    # Non-admins can only update their own open tickets (subject/description)
    if user.get("role") != "admin" and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    if user.get("role") == "admin":
        if "status" in data:
            ticket.status = data["status"]
        if "priority" in data:
            ticket.priority = data["priority"]
        if "assigned_to" in data:
            ticket.assigned_to = data["assigned_to"] or None
        if "resolution" in data:
            ticket.resolution = data["resolution"]

    # Submitter can update subject/description if still open
    if ticket.submitted_by == user["id"] and ticket.status == "open":
        if "subject" in data:
            ticket.subject = data["subject"]
        if "description" in data:
            ticket.description = data["description"]

    db.session.commit()
    return jsonify(success=True, ticket=ticket.to_dict())


# ── API: add comment ─────────────────────────────────────────

@support_bp.route("/api/tickets/comment", methods=["POST"])
@login_required
def add_comment():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    body = (data.get("body") or "").strip()

    if not ticket_id or not body:
        return jsonify(success=False, error="Ticket ID and comment body required"), 400

    ticket = SupportTicket.query.get_or_404(ticket_id)

    # Non-admins can only comment on their own tickets
    if user.get("role") != "admin" and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    comment = TicketComment(
        ticket_id=ticket_id,
        user_id=user["id"],
        body=body,
    )
    db.session.add(comment)
    db.session.commit()

    return jsonify(success=True, comment=comment.to_dict())
