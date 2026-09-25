"""
Serevo — user accounts
=======================
Demo accounts for the decoupled build. In production, this would read
from a database or an auth provider. For now, env-driven demo users.
"""

import os

# Demo accounts — override via env vars for client deployments
ALLOWED_USERS = [
    u.strip() for u in
    os.environ.get("ALLOWED_USERS", "viewer@demo.serevo.app,admin@demo.serevo.app").split(",")
    if u.strip()
]

ADMIN_USERS = [
    u.strip() for u in
    os.environ.get("ADMIN_USERS", "admin@demo.serevo.app").split(",")
    if u.strip()
]

USER_NAMES = {
    "viewer@demo.serevo.app": "Demo Viewer",
    "admin@demo.serevo.app":  "Demo Admin",
}

# Merge any extra names from env (comma-sep "email:Name" pairs)
for pair in os.environ.get("EXTRA_USER_NAMES", "").split(","):
    if ":" in pair:
        email, name = pair.split(":", 1)
        USER_NAMES[email.strip()] = name.strip()
