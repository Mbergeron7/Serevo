# Serevo

**Workforce Management Platform** by Bergeron WFM Solutions.

Forecasting, capacity planning, scheduling, real-time management, and ticketing — built for contact centres.

## Quick start (local dev)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # fill in your Sheet IDs + service account
python run.py                     # http://localhost:5000
```

## Seed demo data

```bash
export $(cat .env | xargs)        # load env vars
python scripts/seed_demo.py       # fills demo Sheets with synthetic data
```

## Deploy to Render

1. Push to a **private** GitHub repo.
2. Create a new **Web Service** on Render, connect the repo.
3. Set start command: `gunicorn "app:create_app()" --bind 0.0.0.0:$PORT`
4. Add every `.env` variable under **Environment**.
5. For the service account JSON, use Render's **Secret Files** feature.

## Project structure

```
serevo/
├── app/                    # Flask application
│   ├── __init__.py         # App factory + brand context processor
│   ├── data_source.py      # Pluggable data layer (generic Sheets / PeopleWare)
│   ├── templates/          # Jinja2 templates
│   └── static/             # CSS, JS, images
├── config/
│   ├── __init__.py         # Centralised config (reads .env, exposes cfg)
│   └── users.py            # Demo user accounts
├── scripts/
│   └── seed_demo.py        # Synthetic data seeder
├── tests/                  # Test suite
├── docs/                   # Internal docs / runbook
├── run.py                  # Dev entry point
├── Procfile                # Render/Heroku start command
├── requirements.txt
├── .env.example            # Template — copy to .env
└── .gitignore
```

## Configuration

All settings are environment-driven — see `.env.example` for the full list. Key switches:

| Variable | Purpose |
|---|---|
| `DATA_SOURCE` | `generic` (Sheets) or `peopleware` (API) |
| `DEMO_MODE` | `true` disables live integrations |
| `BRAND_NAME` | Injected into every template |

## IP hygiene

This repo contains **zero** employer-specific data, names, or Sheet IDs. All demo data is synthetic. See `docs/` for the full IP checklist.
