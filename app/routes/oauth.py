"""
routes/oauth.py — Google OAuth login flow
==========================================
Provides "Sign in with Google" using authlib.
Requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars (or settings).
"""

import logging
import os

from flask import Blueprint, redirect, url_for, session, flash, current_app

log = logging.getLogger("serevo.oauth")

oauth_bp = Blueprint("oauth", __name__)

_oauth = None  # Lazy-initialised OAuth registry


def _get_oauth():
    """Lazy-init the authlib OAuth registry so it's created inside app context."""
    global _oauth
    if _oauth is not None:
        return _oauth

    try:
        from authlib.integrations.flask_client import OAuth
    except ImportError:
        log.warning("authlib not installed — OAuth disabled")
        return None

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    _oauth = OAuth(current_app)
    _oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return _oauth


def is_oauth_enabled():
    """Check if Google OAuth credentials are configured."""
    return bool(os.getenv("GOOGLE_CLIENT_ID")) and bool(os.getenv("GOOGLE_CLIENT_SECRET"))


@oauth_bp.route("/login/google")
def google_login():
    """Redirect to Google's OAuth consent screen."""
    oauth = _get_oauth()
    if not oauth:
        flash("Google sign-in is not configured.", "error")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("oauth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@oauth_bp.route("/login/google/callback")
def google_callback():
    """Handle the OAuth callback from Google."""
    oauth = _get_oauth()
    if not oauth:
        flash("Google sign-in is not configured.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = oauth.google.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.google.userinfo()
    except Exception as e:
        log.warning("Google OAuth error: %s", e)
        flash("Google sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    email = (userinfo.get("email") or "").lower().strip()
    google_id = userinfo.get("sub", "")
    name = userinfo.get("name", "")

    if not email:
        flash("Could not get email from Google.", "error")
        return redirect(url_for("auth.login"))

    # Check allowed domain restriction
    allowed_domain = os.getenv("OAUTH_ALLOWED_DOMAIN", "").strip()
    if allowed_domain and not email.endswith(f"@{allowed_domain}"):
        log.warning("OAuth denied: email=%s not in domain=%s", email, allowed_domain)
        flash(f"Sign-in restricted to @{allowed_domain} accounts.", "error")
        return redirect(url_for("auth.login"))

    from app.models import db, User

    # Look up by OAuth ID first, then by email
    user = User.query.filter_by(oauth_provider="google", oauth_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()

    if user:
        # Link Google to existing account if not already
        if not user.oauth_provider:
            user.oauth_provider = "google"
            user.oauth_id = google_id
        if name and not user.display_name:
            user.display_name = name
        db.session.commit()
    else:
        # Auto-create: default role is supervisor (admin can promote later)
        default_role = os.getenv("OAUTH_DEFAULT_ROLE", "supervisor")
        user = User(
            email=email,
            display_name=name or email.split("@")[0].title(),
            role=default_role,
            oauth_provider="google",
            oauth_id=google_id,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        log.info("OAuth: new user created email=%s role=%s", email, default_role)

    if not user.is_active:
        flash("Your account has been deactivated. Contact an admin.", "error")
        return redirect(url_for("auth.login"))

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    log.info("OAuth login: user_id=%s email=%s provider=google", user.id, email)

    return redirect(url_for("dashboard.index"))
