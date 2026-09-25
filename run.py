"""
Serevo — development entry point
==================================
    python run.py
or via gunicorn:
    gunicorn "app:create_app()"
"""

from app import create_app
from config import cfg

app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=cfg.PORT,
        debug=cfg.is_demo,  # debug only in demo mode
    )
