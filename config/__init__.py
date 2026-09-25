"""
Serevo — centralised configuration
====================================
Every setting the app needs lives here. Templates and modules read from
this instead of calling os.environ directly, so branding, sheet IDs,
and feature flags are defined in one place.

Usage:
    from config import cfg
    print(cfg.BRAND_NAME)           # "Serevo"
    print(cfg.is_demo)              # True / False
    print(cfg.sheet_key("SHEET_KEY"))  # the env var, or raises if missing
"""

import os
from pathlib import Path

# Load .env if python-dotenv is available (local dev convenience)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


class _Config:
    """Read-once, cached configuration object."""

    # ---- branding (injected into every template via context processor) ----
    BRAND_NAME          = os.environ.get("BRAND_NAME", "Serevo")
    BRAND_TAGLINE       = os.environ.get("BRAND_TAGLINE", "Workforce Management Platform")
    BRAND_SUPPORT_EMAIL = os.environ.get("BRAND_SUPPORT_EMAIL", "support@bergeronwfm.com")
    BRAND_ACCENT_COLOR  = os.environ.get("BRAND_ACCENT_COLOR", "#0f766e")

    # ---- data source ----
    DATA_SOURCE = os.environ.get("DATA_SOURCE", "generic").strip().lower()

    # ---- demo mode ----
    DEMO_MODE = os.environ.get("DEMO_MODE", "false").strip().lower() == "true"

    # ---- app ----
    SECRET_KEY = os.environ.get("WFM_SECRET_KEY", "change-me")
    PORT       = int(os.environ.get("PORT", 5000))

    # ---- convenience ----
    @property
    def is_demo(self):
        return self.DEMO_MODE

    @property
    def is_peopleware(self):
        return self.DATA_SOURCE == "peopleware"

    def sheet_key(self, env_var, fallback_var=None, required=True):
        """Return a Google Sheet ID from the environment."""
        val = os.environ.get(env_var, "") or (
            os.environ.get(fallback_var, "") if fallback_var else ""
        )
        if not val and required:
            raise RuntimeError(
                f"Missing required environment variable: {env_var}. "
                f"Set it in your .env or Render dashboard."
            )
        return val

    @property
    def brand_context(self):
        """Dict injected into every Jinja template."""
        return {
            "brand_name":    self.BRAND_NAME,
            "brand_tagline": self.BRAND_TAGLINE,
            "brand_email":   self.BRAND_SUPPORT_EMAIL,
            "brand_accent":  self.BRAND_ACCENT_COLOR,
            "is_demo":       self.is_demo,
        }


cfg = _Config()
