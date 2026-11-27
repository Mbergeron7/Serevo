"""
Organization management — super-admin only.

Provides CRUD for organizations (tenants) and user assignment.
"""

import re
import logging
from flask import Blueprint, render_template, request, session, jsonify
from app.auth import superadmin_required

log = logging.getLogger("serevo.orgs")

org_bp = Blueprint("organizations", __name__)


def _slugify(name):
    """Convert a name to a URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:60]


@org_bp.route("/admin/orgs/")
@superadmin_required
def index():
    from app.models import Organization
    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    return render_template("admin/organizations.html", orgs=orgs)


@org_bp.route("/admin/orgs/create", methods=["POST"])
@superadmin_required
def create():
    from app.models import db, Organization
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "Organization name is required."})

    slug = _slugify(data.get("slug") or name)
    if Organization.query.filter_by(slug=slug).first():
        return jsonify({"success": False, "error": f"Slug '{slug}' is already taken."})
    if Organization.query.filter_by(name=name).first():
        return jsonify({"success": False, "error": f"Organization '{name}' already exists."})

    timezone = (data.get("timezone") or "").strip()
    from app.constants import DEFAULT_TIMEZONE
    if not timezone:
        timezone = DEFAULT_TIMEZONE

    org = Organization(
        name=name,
        slug=slug,
        timezone=timezone,
        domain=(data.get("domain") or "").strip() or None,
    )
    db.session.add(org)
    db.session.commit()
    log.info("Organization created: id=%s name=%s slug=%s", org.id, org.name, org.slug)
    return jsonify({"success": True, "id": org.id, "name": org.name, "slug": org.slug})


@org_bp.route("/admin/orgs/switch", methods=["POST"])
@superadmin_required
def switch():
    data = request.get_json(silent=True) or {}
    org_id = data.get("org_id")
    if org_id:
        from app.models import Organization
        org = Organization.query.get(org_id)
        if not org:
            return jsonify({"success": False, "error": "Organization not found."})
        session["active_org_id"] = org.id
        log.info("Super-admin switched to org: id=%s name=%s", org.id, org.name)
        return jsonify({"success": True, "org_id": org.id, "org_name": org.name})
    else:
        # Clear — go back to "all orgs" / platform view
        session.pop("active_org_id", None)
        return jsonify({"success": True, "org_id": None, "org_name": "All Organizations"})


@org_bp.route("/admin/orgs/<int:org_id>/users")
@superadmin_required
def org_users(org_id):
    from app.models import Organization, User
    org = Organization.query.get_or_404(org_id)
    users = User.query.filter_by(organization_id=org_id).order_by(User.display_name).all()
    unassigned = User.query.filter(
        (User.organization_id.is_(None)) | (User.organization_id != org_id)
    ).filter_by(is_active=True).order_by(User.display_name).all()
    return jsonify({
        "success": True,
        "org": {"id": org.id, "name": org.name},
        "users": [{"id": u.id, "email": u.email, "name": u.display_name, "role": u.role} for u in users],
        "unassigned": [{"id": u.id, "email": u.email, "name": u.display_name, "role": u.role} for u in unassigned],
    })


@org_bp.route("/admin/orgs/<int:org_id>/assign-user", methods=["POST"])
@superadmin_required
def assign_user(org_id):
    from app.models import db, Organization, User
    org = Organization.query.get_or_404(org_id)
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required."})
    user = User.query.get(user_id)
    if not user:
        return jsonify({"success": False, "error": "User not found."})

    action = data.get("action", "assign")
    if action == "remove":
        user.organization_id = None
        log.info("User %s removed from org %s", user.email, org.name)
    else:
        user.organization_id = org.id
        log.info("User %s assigned to org %s", user.email, org.name)
    db.session.commit()
    return jsonify({"success": True})


@org_bp.route("/admin/orgs/<int:org_id>/update", methods=["POST"])
@superadmin_required
def update_org(org_id):
    from app.models import db, Organization
    org = Organization.query.get_or_404(org_id)
    data = request.get_json(silent=True) or {}

    if data.get("name"):
        org.name = data["name"].strip()
    if data.get("timezone"):
        org.timezone = data["timezone"].strip()
    if "domain" in data:
        org.domain = (data["domain"] or "").strip() or None
    if "is_active" in data:
        org.is_active = bool(data["is_active"])

    db.session.commit()
    return jsonify({"success": True})


@org_bp.route("/admin/orgs/<int:org_id>/delete", methods=["POST"])
@superadmin_required
def delete_org(org_id):
    from app.models import db, Organization, User
    org = Organization.query.get_or_404(org_id)
    # Don't delete if users are assigned
    user_count = User.query.filter_by(organization_id=org_id).count()
    if user_count > 0:
        return jsonify({"success": False, "error": f"Cannot delete — {user_count} user(s) still assigned."})
    db.session.delete(org)
    db.session.commit()
    log.info("Organization deleted: id=%s name=%s", org_id, org.name)
    return jsonify({"success": True})
