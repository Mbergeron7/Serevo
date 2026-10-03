"""
Approvals — unified inbox for managers to approve/deny PTO, shift bids, and shift swaps.
"""
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, render_template
from app.auth import login_required, get_current_user
from app.models import (
    db, PTOEntry, ShiftBid, ShiftPost, ShiftSwapRequest, Employee, Schedule,
)

log = logging.getLogger(__name__)
approvals_bp = Blueprint("approvals", __name__, url_prefix="/approvals")


def _require_manager():
    """Return current user dict if admin or supervisor, else None."""
    user = get_current_user()
    if not user:
        return None
    if user.get("role") not in ("admin", "supervisor"):
        return None
    return user


# ── Page ────────────────────────────────────────────────────────

@approvals_bp.route("/")
@login_required
def index():
    user = get_current_user()
    if not user or user.get("role") not in ("admin", "supervisor"):
        from flask import redirect
        return redirect("/")
    return render_template("approvals/index.html")


# ── Pending counts (badge) ─────────────────────────────────────

@approvals_bp.route("/api/counts", methods=["POST"])
@login_required
def pending_counts():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_approvals_counts
        return jsonify(success=True, **get_demo_approvals_counts())
    try:
        pto = PTOEntry.query.filter_by(approval_status="pending").count()
        bids = ShiftBid.query.filter_by(status="pending").count()
        swaps = ShiftSwapRequest.query.filter(
            ShiftSwapRequest.status.in_(["accepted", "pending"])
        ).count()
        return jsonify({
            "success": True,
            "pto": pto, "bids": bids, "swaps": swaps,
            "total": pto + bids + swaps,
        })
    except Exception as e:
        log.exception("Pending counts error")
        return jsonify({"success": False, "error": str(e)})


# ── PTO list + approve/deny ────────────────────────────────────

@approvals_bp.route("/api/pto", methods=["POST"])
@login_required
def list_pto():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_approvals_pto
        status_filter = (request.json or {}).get("status", "pending")
        return jsonify(success=True, entries=get_demo_approvals_pto(status_filter))
    try:
        status_filter = (request.json or {}).get("status", "pending")
        q = PTOEntry.query.join(Employee, PTOEntry.employee_id == Employee.id)
        if status_filter != "all":
            q = q.filter(PTOEntry.approval_status == status_filter)
        entries = q.order_by(PTOEntry.start_date.desc()).limit(100).all()
        result = []
        for e in entries:
            emp = Employee.query.get(e.employee_id)
            tot = e.time_off_type
            result.append({
                "id": e.id,
                "employee": emp.full_name if emp else f"#{e.employee_id}",
                "employee_id": e.employee_id,
                "start_date": e.start_date.isoformat(),
                "end_date": e.end_date.isoformat(),
                "pto_type": e.pto_type,
                "time_off_type": tot.name if tot else "PTO",
                "note": e.note or "",
                "status": e.approval_status,
            })
        return jsonify({"success": True, "entries": result})
    except Exception as e:
        log.exception("PTO list error")
        return jsonify({"success": False, "error": str(e)})


@approvals_bp.route("/api/pto/decide", methods=["POST"])
@login_required
def decide_pto():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        entry_id = payload.get("id")
        decision = payload.get("decision")  # approved | denied
        if decision not in ("approved", "denied"):
            return jsonify({"success": False, "error": "Invalid decision"})
        entry = PTOEntry.query.get(entry_id)
        if not entry:
            return jsonify({"success": False, "error": "Not found"})
        entry.approval_status = decision
        entry.reviewed_by = user["id"]
        entry.reviewed_at = datetime.now(timezone.utc)
        db.session.commit()
        # Notify the employee
        _notify_employee(entry.employee_id, "pto", decision, entry)
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.exception("PTO decide error")
        return jsonify({"success": False, "error": str(e)})


# ── Shift bids list + approve/deny ─────────────────────────────

@approvals_bp.route("/api/bids", methods=["POST"])
@login_required
def list_bids():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_approvals_bids
        status_filter = (request.json or {}).get("status", "pending")
        return jsonify(success=True, bids=get_demo_approvals_bids(status_filter))
    try:
        status_filter = (request.json or {}).get("status", "pending")
        q = ShiftBid.query.join(ShiftPost, ShiftBid.shift_post_id == ShiftPost.id)
        if status_filter != "all":
            q = q.filter(ShiftBid.status == status_filter)
        bids = q.order_by(ShiftBid.created_at.desc()).limit(100).all()
        result = []
        for b in bids:
            emp = Employee.query.get(b.employee_id)
            sp = b.shift_post
            result.append({
                "id": b.id,
                "employee": emp.full_name if emp else f"#{b.employee_id}",
                "employee_id": b.employee_id,
                "shift_date": sp.schedule_date.isoformat() if sp else "",
                "shift_start": sp.shift_start.strftime("%H:%M") if sp and sp.shift_start else "",
                "shift_end": sp.shift_end.strftime("%H:%M") if sp and sp.shift_end else "",
                "hours": sp.hours if sp else 0,
                "preference": b.preference,
                "status": b.status,
                "created_at": b.created_at.isoformat() if b.created_at else "",
            })
        return jsonify({"success": True, "bids": result})
    except Exception as e:
        log.exception("Bids list error")
        return jsonify({"success": False, "error": str(e)})


@approvals_bp.route("/api/bids/decide", methods=["POST"])
@login_required
def decide_bid():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        bid_id = payload.get("id")
        decision = payload.get("decision")  # accepted | declined
        if decision not in ("accepted", "declined"):
            return jsonify({"success": False, "error": "Invalid decision"})
        bid = ShiftBid.query.get(bid_id)
        if not bid:
            return jsonify({"success": False, "error": "Not found"})
        bid.status = decision
        if decision == "accepted":
            # Assign the shift to this employee
            sp = bid.shift_post
            if sp:
                sp.assigned_to = bid.employee_id
                sp.status = "assigned"
                # Decline all other bids for this shift
                ShiftBid.query.filter(
                    ShiftBid.shift_post_id == sp.id,
                    ShiftBid.id != bid.id,
                ).update({"status": "declined"})
        db.session.commit()
        _notify_employee(bid.employee_id, "bid", decision, bid)
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.exception("Bid decide error")
        return jsonify({"success": False, "error": str(e)})


# ── Shift swaps list + approve/deny ────────────────────────────

@approvals_bp.route("/api/swaps", methods=["POST"])
@login_required
def list_swaps():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if user.get("is_demo"):
        from app.demo_data import get_demo_approvals_swaps
        status_filter = (request.json or {}).get("status", "accepted")
        return jsonify(success=True, swaps=get_demo_approvals_swaps(status_filter))
    try:
        status_filter = (request.json or {}).get("status", "accepted")
        q = ShiftSwapRequest.query
        if status_filter == "needs_approval":
            q = q.filter(ShiftSwapRequest.status.in_(["accepted", "pending"]))
        elif status_filter != "all":
            q = q.filter(ShiftSwapRequest.status == status_filter)
        swaps = q.order_by(ShiftSwapRequest.created_at.desc()).limit(100).all()
        result = []
        for s in swaps:
            rs = s.requester_sched
            ts = s.target_sched
            result.append({
                "id": s.id,
                "requester": s.requester.full_name if s.requester else "",
                "target": s.target.full_name if s.target else "Open",
                "requester_date": rs.schedule_date.isoformat() if rs else "",
                "requester_shift": f"{rs.start_time.strftime('%H:%M')}-{rs.end_time.strftime('%H:%M')}" if rs and rs.start_time else "",
                "target_date": ts.schedule_date.isoformat() if ts else "",
                "target_shift": f"{ts.start_time.strftime('%H:%M')}-{ts.end_time.strftime('%H:%M')}" if ts and ts.start_time else "",
                "reason": s.reason or "",
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else "",
            })
        return jsonify({"success": True, "swaps": result})
    except Exception as e:
        log.exception("Swaps list error")
        return jsonify({"success": False, "error": str(e)})


@approvals_bp.route("/api/swaps/decide", methods=["POST"])
@login_required
def decide_swap():
    user = _require_manager()
    if not user:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    try:
        payload = request.get_json(silent=True) or {}
        swap_id = payload.get("id")
        decision = payload.get("decision")  # approved | declined
        if decision not in ("approved", "declined"):
            return jsonify({"success": False, "error": "Invalid decision"})
        swap = ShiftSwapRequest.query.get(swap_id)
        if not swap:
            return jsonify({"success": False, "error": "Not found"})
        swap.status = decision
        swap.reviewed_by = user["id"]
        if decision == "approved" and swap.requester_sched and swap.target_sched:
            # Actually swap the schedule assignments
            rs = swap.requester_sched
            ts = swap.target_sched
            rs.employee_id, ts.employee_id = ts.employee_id, rs.employee_id
        db.session.commit()
        # Notify both employees
        _notify_employee(swap.requester_id, "swap", decision, swap)
        if swap.target_id:
            _notify_employee(swap.target_id, "swap", decision, swap)
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        log.exception("Swap decide error")
        return jsonify({"success": False, "error": str(e)})


# ── Notification helper ────────────────────────────────────────

def _notify_employee(employee_id, request_type, decision, obj):
    """Send a notification to the employee about the decision."""
    from app.routes.notifications import notify
    from app.models import User
    try:
        # Find the user linked to this employee
        user = User.query.filter_by(employee_id=employee_id).first()
        if not user:
            return
        labels = {
            "pto": {"approved": "PTO Request Approved", "denied": "PTO Request Denied"},
            "bid": {"accepted": "Shift Bid Accepted", "declined": "Shift Bid Declined"},
            "swap": {"approved": "Shift Swap Approved", "declined": "Shift Swap Declined"},
        }
        title = labels.get(request_type, {}).get(decision, "Request Updated")
        messages = {
            "pto": f"Your time-off request ({obj.start_date.isoformat()} to {obj.end_date.isoformat()}) has been {decision}.",
            "bid": f"Your shift bid has been {decision}.",
            "swap": f"Your shift swap request has been {decision}.",
        }
        msg = messages.get(request_type, "Your request has been updated.")
        cat = "success" if decision in ("approved", "accepted") else "warning"
        notify(user.id, title, msg, category=cat, link="/portal/")
    except Exception:
        log.exception("Failed to notify employee about approval decision")
