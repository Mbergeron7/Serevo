"""
WFM Tickets — client-facing ticketing tool for team leads to submit
schedule changes, OT requests, and other WFM needs to WFM analysts.

Access: users with wfm_access=True can submit tickets.
        Admins/supervisors (WFM analysts) can manage all tickets.
"""
import csv
import io
import logging
from datetime import datetime, timezone, date, timedelta

from flask import Blueprint, render_template, request, jsonify, Response
from app.auth import login_required, get_current_user
from app.models import db, WfmTicket, WfmTicketComment, WfmTicketHistory, User
from sqlalchemy import func, case

log = logging.getLogger(__name__)
wfm_tickets_bp = Blueprint("wfm_tickets", __name__, url_prefix="/wfm-tickets")


def _demo_guard():
    """Return a mock-success JSON response if the current user is a demo user, else None."""
    user = get_current_user()
    if user and user.get("is_demo"):
        return jsonify(success=True, demo=True, message="Changes are not saved in demo mode.")
    return None


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


def _log_history(ticket_id, user_id, action, field=None, old_value=None, new_value=None):
    """Record an audit history entry."""
    entry = WfmTicketHistory(
        ticket_id=ticket_id,
        user_id=user_id,
        action=action,
        field=field,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
    )
    db.session.add(entry)


# ── Main page ────────────────────────────────────────────────

@wfm_tickets_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not _has_wfm_access(user):
        return render_template("403.html"), 403
    # Build analyst list for assign dropdown
    analysts = []
    if _is_wfm_analyst(user):
        analysts = User.query.filter(
            User.is_active == True,
            User.role.in_(["admin", "supervisor"])
        ).order_by(User.display_name).all()
        analysts = [{"id": a.id, "name": a.display_name} for a in analysts]
    return render_template("wfm_tickets/index.html", user=user,
                           is_analyst=_is_wfm_analyst(user),
                           analysts=analysts)


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
    show_archived = data.get("archived", False)
    show_deleted = data.get("deleted", False)
    search_text = (data.get("search") or "").strip()

    query = WfmTicket.query

    # Team leads (non-analyst) see only their own tickets
    if not _is_wfm_analyst(user):
        query = query.filter_by(submitted_by=user["id"])

    # Active / archived / deleted filtering
    if show_deleted:
        query = query.filter_by(is_deleted=True)
    elif show_archived:
        query = query.filter_by(is_archived=True, is_deleted=False)
    else:
        query = query.filter_by(is_archived=False, is_deleted=False)

    if status_filter and status_filter != "all":
        query = query.filter_by(status=status_filter)
    if category_filter and category_filter != "all":
        query = query.filter_by(category=category_filter)
    if search_text:
        like = f"%{search_text}%"
        query = query.filter(
            db.or_(
                WfmTicket.subject.ilike(like),
                WfmTicket.affected_agents.ilike(like),
                WfmTicket.description.ilike(like),
            )
        )

    query = query.order_by(WfmTicket.created_at.desc())

    # Pagination
    page = max(int(data.get("page", 1)), 1)
    per_page = min(int(data.get("per_page", 50)), 200)
    total = query.count()
    tickets = query.offset((page - 1) * per_page).limit(per_page).all()

    strip = not _is_wfm_analyst(user)
    return jsonify(success=True, tickets=[t.to_dict(strip_internal=strip) for t in tickets],
                   total=total, page=page, per_page=per_page)


# ── API: create ticket ───────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/create", methods=["POST"])
@login_required
def create_ticket():
    dg = _demo_guard()
    if dg:
        return dg
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
    db.session.flush()

    _log_history(ticket.id, user["id"], "created")
    db.session.commit()

    log.info(f"WFM ticket #{ticket.id} created by {user.get('email')} [{category}]")

    # Notify WFM analysts about new ticket
    try:
        from app.routes.notifications import notify_all_admins
        notify_all_admins(
            f"WFM Ticket: {subject}",
            f"New {priority} priority WFM ticket ({category}) from {user.get('display_name', user.get('email', 'a user'))}.",
            category="info",
            link="/wfm-tickets/",
        )
    except Exception:
        log.warning("Failed to notify analysts of WFM ticket", exc_info=True)

    return jsonify(success=True, ticket=ticket.to_dict())


# ── API: get single ticket with comments ─────────────────────

@wfm_tickets_bp.route("/api/tickets/<int:ticket_id>", methods=["GET"])
@login_required
def get_ticket(ticket_id):
    user = get_current_user()
    if not _has_wfm_access(user):
        return jsonify(success=False, error="No WFM access"), 403

    if user.get("is_demo"):
        from app.demo_data import get_demo_wfm_ticket
        data = get_demo_wfm_ticket(ticket_id)
        return jsonify(success=True, **data)

    ticket = WfmTicket.query.get_or_404(ticket_id)

    # Non-analysts can only view their own tickets
    is_analyst = _is_wfm_analyst(user)
    if not is_analyst and ticket.submitted_by != user["id"]:
        return jsonify(success=False, error="Forbidden"), 403

    comments = ticket.comments
    # Filter out internal comments for non-analysts
    if not is_analyst:
        comments = [c for c in comments if not c.is_internal]

    return jsonify(success=True, ticket=ticket.to_dict(strip_internal=not is_analyst),
                   comments=[c.to_dict() for c in comments])


# ── API: update ticket (analysts manage, submitters edit own) ─

@wfm_tickets_bp.route("/api/tickets/update", methods=["POST"])
@login_required
def update_ticket():
    dg = _demo_guard()
    if dg:
        return dg
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
        if "status" in data and data["status"] != ticket.status:
            old_status = ticket.status
            ticket.status = data["status"]
            _log_history(ticket.id, user["id"], "status_change", "status", old_status, data["status"])
            # Auto-stamp closed_at / closed_by
            if data["status"] in ("closed", "resolved") and not ticket.closed_at:
                ticket.closed_at = _utcnow()
                ticket.closed_by = user["id"]
            elif data["status"] in ("open", "in_progress") and ticket.closed_at:
                # Re-opened — clear closed fields
                ticket.closed_at = None
                ticket.closed_by = None
        if "priority" in data and data["priority"] != ticket.priority:
            _log_history(ticket.id, user["id"], "priority_change", "priority", ticket.priority, data["priority"])
            ticket.priority = data["priority"]
        if "assigned_to" in data:
            new_assignee = data["assigned_to"] or None
            if new_assignee != ticket.assigned_to:
                old_name = ticket.assignee.display_name if ticket.assignee else None
                ticket.assigned_to = new_assignee
                new_name = User.query.get(new_assignee).display_name if new_assignee else None
                _log_history(ticket.id, user["id"], "assigned", "assigned_to", old_name, new_name)
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

    # Notify the submitter when an analyst changes the status
    if "status" in data and ticket.submitted_by != user["id"]:
        try:
            from app.routes.notifications import notify
            notify(
                ticket.submitted_by,
                f"WFM Ticket Updated: {ticket.subject}",
                f"Your WFM ticket status changed to '{ticket.status}'.",
                category="success" if ticket.status in ("resolved", "closed") else "info",
                link="/wfm-tickets/",
            )
        except Exception:
            log.warning("Failed to notify submitter of WFM ticket status change", exc_info=True)

    return jsonify(success=True, ticket=ticket.to_dict(strip_internal=not _is_wfm_analyst(user)))


# ── API: quick assign ────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/quick-assign", methods=["POST"])
@login_required
def quick_assign():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    assignee_id = data.get("assigned_to") or None

    if not ticket_id:
        return jsonify(success=False, error="Missing ticket_id"), 400

    ticket = WfmTicket.query.get_or_404(ticket_id)
    old_name = ticket.assignee.display_name if ticket.assignee else None
    ticket.assigned_to = assignee_id
    new_name = User.query.get(assignee_id).display_name if assignee_id else None
    _log_history(ticket.id, user["id"], "assigned", "assigned_to", old_name, new_name)
    db.session.commit()

    return jsonify(success=True)


# ── API: archive ticket ──────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/archive", methods=["POST"])
@login_required
def archive_ticket():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    ticket = WfmTicket.query.get_or_404(ticket_id)
    ticket.is_archived = True
    _log_history(ticket.id, user["id"], "archived")
    db.session.commit()
    return jsonify(success=True)


# ── API: restore from archive ────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/restore", methods=["POST"])
@login_required
def restore_ticket():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    ticket = WfmTicket.query.get_or_404(ticket_id)
    ticket.is_archived = False
    ticket.is_deleted = False
    _log_history(ticket.id, user["id"], "restored")
    db.session.commit()
    return jsonify(success=True)


# ── API: soft-delete ticket ──────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/delete", methods=["POST"])
@login_required
def delete_ticket():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    ticket = WfmTicket.query.get_or_404(ticket_id)
    ticket.is_deleted = True
    _log_history(ticket.id, user["id"], "deleted")
    db.session.commit()
    return jsonify(success=True)


# ── API: permanently delete ticket ───────────────────────────

@wfm_tickets_bp.route("/api/tickets/permanent-delete", methods=["POST"])
@login_required
def permanent_delete_ticket():
    dg = _demo_guard()
    if dg:
        return dg
    user = get_current_user()
    if user.get("role") != "admin":
        return jsonify(success=False, error="Admin only"), 403

    data = request.get_json(silent=True) or {}
    ticket_id = data.get("ticket_id")
    ticket = WfmTicket.query.get_or_404(ticket_id)
    db.session.delete(ticket)
    db.session.commit()
    return jsonify(success=True)


# ── API: ticket history ──────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/history", methods=["POST"])
@login_required
def ticket_history():
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    if user and user.get("is_demo"):
        return jsonify(success=True, history=[])

    data = request.get_json(silent=True) or {}
    ticket_filter = data.get("ticket_id")
    limit = min(int(data.get("limit", 200)), 1000)

    query = WfmTicketHistory.query.order_by(WfmTicketHistory.created_at.desc())
    if ticket_filter:
        query = query.filter_by(ticket_id=ticket_filter)

    entries = query.limit(limit).all()
    return jsonify(success=True, history=[e.to_dict() for e in entries])


# ── API: analytics ───────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/analytics", methods=["POST"])
@login_required
def ticket_analytics():
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    if user.get("is_demo"):
        return _demo_analytics()

    data = request.get_json(silent=True) or {}
    date_from = data.get("from")
    date_to = data.get("to")

    query = WfmTicket.query.filter_by(is_deleted=False)
    if date_from:
        try:
            query = query.filter(WfmTicket.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.filter(WfmTicket.created_at < dt + timedelta(days=1))
        except ValueError:
            pass

    tickets = query.all()

    # Status breakdown
    status_counts = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
    # Category breakdown
    category_counts = {}
    # Volume by day
    volume_by_day = {}
    # Workload by assignee
    workload = {}
    # Resolution time tracking
    resolution_hours = []

    for t in tickets:
        status_counts[t.status] = status_counts.get(t.status, 0) + 1

        cat = t.category or "general"
        if cat not in category_counts:
            category_counts[cat] = {"open": 0, "closed": 0}
        if t.status in ("closed", "resolved"):
            category_counts[cat]["closed"] += 1
        else:
            category_counts[cat]["open"] += 1

        if t.created_at:
            day = t.created_at.strftime("%Y-%m-%d")
            volume_by_day[day] = volume_by_day.get(day, 0) + 1

        if t.assigned_to and t.assignee:
            name = t.assignee.display_name
            if name not in workload:
                workload[name] = {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
            workload[name][t.status] = workload[name].get(t.status, 0) + 1

        if t.closed_at and t.created_at:
            delta = (t.closed_at - t.created_at).total_seconds() / 3600
            resolution_hours.append(delta)

    total = len(tickets)
    closed_total = status_counts.get("closed", 0) + status_counts.get("resolved", 0)
    closure_rate = round(closed_total / total * 100, 1) if total > 0 else 0
    avg_resolution = round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None

    # Busiest day
    busiest_day = None
    if volume_by_day:
        busiest_day = max(volume_by_day, key=volume_by_day.get)
        busiest_day = f"{busiest_day} ({volume_by_day[busiest_day]})"

    # Top request type
    top_type = None
    if category_counts:
        top_cat = max(category_counts, key=lambda c: category_counts[c]["open"] + category_counts[c]["closed"])
        top_type = top_cat

    return jsonify(success=True, analytics={
        "total": total,
        "status": status_counts,
        "closure_rate": closure_rate,
        "avg_resolution_hours": avg_resolution,
        "busiest_day": busiest_day,
        "top_request_type": top_type,
        "volume_by_day": dict(sorted(volume_by_day.items())),
        "category_breakdown": category_counts,
        "workload": workload,
    })


def _demo_analytics():
    """Return plausible analytics for demo mode."""
    import random
    today = date.today()
    volume = {}
    for i in range(30):
        d = (today - timedelta(days=29 - i)).isoformat()
        volume[d] = random.randint(1, 8)
    cats = {
        "schedule_change": {"open": 4, "closed": 12},
        "overtime": {"open": 2, "closed": 8},
        "time_off_exception": {"open": 1, "closed": 5},
        "shift_swap": {"open": 1, "closed": 3},
        "general": {"open": 2, "closed": 4},
    }
    return jsonify(success=True, analytics={
        "total": 42,
        "status": {"open": 6, "in_progress": 4, "resolved": 3, "closed": 29},
        "closure_rate": 76.2,
        "avg_resolution_hours": 18.4,
        "busiest_day": f"{(today - timedelta(days=3)).isoformat()} (8)",
        "top_request_type": "schedule_change",
        "volume_by_day": volume,
        "category_breakdown": cats,
        "workload": {
            "WFM Analyst": {"open": 3, "in_progress": 2, "resolved": 1, "closed": 14},
            "Admin User": {"open": 2, "in_progress": 1, "resolved": 1, "closed": 10},
        },
    })


# ── API: CSV download ────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/download-csv", methods=["GET"])
@login_required
def download_csv():
    user = get_current_user()
    if not _is_wfm_analyst(user):
        return jsonify(success=False, error="Analyst only"), 403

    tickets = WfmTicket.query.filter_by(is_deleted=False).order_by(WfmTicket.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Ticket ID", "Subject", "Category", "Priority", "Status",
        "Submitted By", "Assigned To", "Affected Agents", "Affected Date",
        "Created At", "Closed At", "Closed By", "Duration (hrs)", "Archived"
    ])

    for t in tickets:
        duration = None
        if t.closed_at and t.created_at:
            duration = round((t.closed_at - t.created_at).total_seconds() / 3600, 1)
        writer.writerow([
            t.id, t.subject, t.category, t.priority, t.status,
            t.submitter.display_name if t.submitter else "",
            t.assignee.display_name if t.assignee else "",
            t.affected_agents or "",
            t.affected_date.isoformat() if t.affected_date else "",
            t.created_at.isoformat() if t.created_at else "",
            t.closed_at.isoformat() if t.closed_at else "",
            t.closer.display_name if t.closer else "",
            duration or "",
            "Yes" if t.is_archived else "No",
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=wfm_tickets_{date.today().isoformat()}.csv"},
    )


# ── API: add comment ─────────────────────────────────────────

@wfm_tickets_bp.route("/api/tickets/comment", methods=["POST"])
@login_required
def add_comment():
    dg = _demo_guard()
    if dg:
        return dg
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
    _log_history(ticket_id, user["id"], "commented")
    db.session.commit()

    return jsonify(success=True, comment=comment.to_dict())


# ── API: toggle WFM access for a user (admin only) ──────────

@wfm_tickets_bp.route("/api/users/wfm-access", methods=["POST"])
@login_required
def toggle_wfm_access():
    dg = _demo_guard()
    if dg:
        return dg
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

    if user.get("is_demo"):
        demo_users = [
            {"id": i + 1, "email": f"user{i+1}@demo.serevo.app", "display_name": n,
             "role": "agent", "wfm_access": i < 5}
            for i, n in enumerate(["Alex Morgan", "Jordan Rivera", "Casey Chen",
                                   "Sam Patel", "Taylor Kim", "Drew Nguyen",
                                   "Riley Brooks", "Priya Sharma"])
        ]
        return jsonify(success=True, users=demo_users)

    users = User.query.filter_by(is_active=True).order_by(User.display_name).all()
    return jsonify(success=True, users=[{
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "wfm_access": getattr(u, "wfm_access", False) or False,
    } for u in users])
