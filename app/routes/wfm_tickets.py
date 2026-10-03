"""
WFM Tickets — client-facing ticketing tool for team leads to submit
schedule changes, OT requests, and other WFM needs to WFM analysts.

Access: users with wfm_access=True can submit tickets.
        Admins/supervisors (WFM analysts) can manage all tickets.
"""
import logging
from datetime import datetime, timezone, date

from flask import Blueprint, render_template, request, jsonify
from app.auth import login_required, get_current_user
from app.models import db, WfmTicket, WfmTicketComment, User

log = logging.getLogger(__name__)
wfm_tickets_bp = Blueprint("wfm_tickets", __name__, url_prefix="/wfm-tickets")


def _utcnow():
    return datetime.now(timezone.utc)


def _has_wfm_access(user):
    """Check if user has WFM ticket access (wfm_access flag or admin/supervisor)."""
    role = user.get("role", "")
    if role in ("admin", "supervisor"):
        return True
    return bool(user.get("wfm_access"))


def _is_wfm_analyst(user):
    """WFM analysts are admins and supervisors — they manage tickets."""
    return user.get("role") in ("admin", "supervisor")


# ── Main page ────────────────────────────────────────────────

@wfm_tickets_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not _has_wfm_access(user):
        return render_template("403.html"), 403
    return render_template("wfm_tickets/index.html", user=user,
                           is_analyst=_is_wfm_analyst(user))


# ── API: list tickets ────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets", methods=["POST"])
@login_required
def list_tickets():
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    if user and user.get("is_demo"):
        from app.demo_data import get_demo_wfm_tickets
        return jsonify(success=True, tickets=get_demo_wfm_tickets(), total=3, page=1, per_page=50)

    data = request.get_json(silent=True) or {}
    status_filter = data.get("status")
    category_filter = data.get("category")

    query = WfmTicket.query

    # Team leads (non-analyst) see only their own tickets
    if not _is_wfm_analyst(user):
        query = query.filter_by(submitted_by=user["id"])

    if status_filter and status_filter != "all":
        query = query.filter_by(status=status_filter)
    if category_filter and category_filter != "all":
        query = query.filter_by(category=category_filter)

    query = query.order_by(WfmTicket.created_at.desc())

    # Pagination
    page = max(int(data.get("page", 1)), 1)
    per_page = min(int(data.get("per_page", 50)), 200)
    total = query.count()
    tickets = query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify(success=True, tickets=[t.to_dict() for t in tickets],
                   total=total, page=page, per_page=per_page)


# ── API: create ticket ───────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/create", methods=["POST"])
@login_required
def create_ticket():
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    data = request.get_json(silent=True) or {}

    subject = (data.get("subject") or "").strip()
    description = (data.get("description") or "").strip()
    category = data.get("category", "general")
    priority = data.get("priority", "medium")

    if not subject or not description:
        return jsonify(success=False, error="Subject and description are required"), 400

    ticket = WfmTicket(
        subject=subject,
        description=description,
        category=category,
        priority=priority,
        submitted_by=user["id"],
        affected_agents=data.get("affected_agents"),
    )

    # Parse affected_date if provided
    affected_date = data.get("affected_date")
    if affected_date:
        try:
            ticket.affected_date = date.fromisoformat(affected_date)
        except (ValueError, TypeError):
            pass

    db.session.add(ticket)
    db.session.commit()

    log.info(f"WFM ticket #{ticket.id} created by {user.get('email')} [{category}]")
    return jsonify(success=True, ticket=ticket.to_dict())


# ── API: get single ticket with comments ─────────────────────

@wfm_tickets_bp.route("/api/tickets/<int:ticket_id>", methods=["GET"])
@login_required
def get_ticket(ticket_id):
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    ticket = WfmTicket.query.get_or_404(ticket_id)

    # Non-analysts can only view their own tickets
    if not _is_wfm_analyst(user) and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    comments = ticket.comments
    # Filter out internal comments for non-analysts
    if not _is_wfm_analyst(user):
        comments = [c for c in comments if not c.is_internal]

    return jsonify(success=True, ticket=ticket.to_dict(),
                   comments=[c.to_dict() for c in comments])


# ── API: update ticket (analysts manage, submitters edit own) ─

@wfm_tickets_bp.route("/api/tickets/update", methods=["POST"])
@login_required
def update_ticket():
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("id")

    if not ticket_id:
        return jsonify(success=False, error="Missing ticket id"), 400

    ticket = WfmTicket.query.get_or_404(ticket_id)

    # Non-analysts can only update their own open tickets
    if not _is_wfm_analyst(user) and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    if _is_wfm_analyst(user):
        if "status" in data:
            ticket.status = data["status"]
        if "priority" in data:
            ticket.priority = data["priority"]
        if "assigned_to" in data:
            ticket.assigned_to = data["assigned_to"] or None
        if "resolution" in data:
            ticket.resolution = data["resolution"]
        if "internal_note" in data:
            ticket.internal_note = data["internal_note"]

    # Submitter can update subject/description if still open
    if ticket.submitted_by == user["id"] and ticket.status == "open":
        if "subject" in data:
            ticket.subject = data["subject"]
        if "description" in data:
            ticket.description = data["description"]

    db.session.commit()
    return jsonify(success=True, ticket=ticket.to_dict())


# ── API: add comment ─────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/comment", methods=["POST"])
@login_required
def add_comment():
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    body = (data.get("body") or "").strip()

    if not ticket_id or not body:
        return jsonify(success=False, error="Ticket ID and comment body required"), 400

    ticket = WfmTicket.query.get_or_404(ticket_id)

    # Non-analysts can only comment on their own tickets
    if not _is_wfm_analyst(user) and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    # Only analysts can post internal comments
    is_internal = bool(data.get("is_internal")) and _is_wfm_analyst(user)

    comment = WfmTicketComment(
        ticket_id=ticket_id,
        user_id=user["id"],
        body=body,
        is_internal=is_internal,
    )
    db.session.add(comment)
    db.session.commit()

    return jsonify(success=True, comment=comment.to_dict())


# ── API: toggle WFM access for a user (admin only) ──────────

@wfm_tickets_bp.route("/api/users/wfm-access", methods=["POST"])
@login_required
def toggle_wfm_access():
    user = get_current_user()
    if user.get("role") != "admin":
        return jsonify(success=False, error="Admin only"), 403

    data = request.get_json(silent=True) or {}
    target_user_id = data.get("user_id")
    grant = data.get("grant", True)

    if not target_user_id:
        return jsonify(success=False, error="Missing user_id"), 400

    target = User.query.get_or_404(target_user_id)
    target.wfm_access = bool(grant)
    db.session.commit()

    log.info(f"WFM access {'granted to' if grant else 'revoked from'} user {target.email} by {user.get('email')}")
    return jsonify(success=True, user_id=target.id, wfm_access=target.wfm_access)


# ── API: list users with WFM access info (admin only) ────────

@wfm_tickets_bp.route("/api/users/wfm-list", methods=["POST"])
@login_required
def list_wfm_users():
    user = get_current_user()
    if user.get("role") != "admin":
        return jsonify(success=False, error="Admin only"), 403

    users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    return jsonify(success=True, users=[{
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "wfm_access": getattr(u, "wfm_access", False) or False,
    } for u in users])
