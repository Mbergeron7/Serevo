# Serevo WFM Platform — Session Handoff

## Project Overview
Serevo is a freelance WFM (Workforce Management) web platform Mike is building. It's a Flask + SQLAlchemy app deployed on Render.

- **Device path**: `C:\Users\mikeb\OneDrive\Freelance Project\Serevo`
- **GitHub repo**: `github.com/Mbergeron7/Serevo`
- **Render deploy**: auto-deploys from `main` branch
- **Render Build Command**: `pip install -r requirements.txt && flask db upgrade`
- **Demo emails**: `{"demo@serevo.app", "admin@demo.serevo.app", "viewer@demo.serevo.app"}`
- **Service account email**: `serevo-sheets@serevo.iam.gserviceaccount.com`
- **PeopleWare API token**: `MGE1M2YwNGNiN2JlY2NjZDNhNTM0ODZhYTA5YzdkZDQ=` — used AS-IS as Bearer token (NOT base64-decoded)

## Standing Constraints (preserve always)
- **No SVI references anywhere in the product** — fully white-labeled
- **No "PeopleWare" in UI** — use "Import from API" / "connected system"
- **Git push commands must be provided every time files are synced to PC**
- **.env file cannot be written via device_commit_files** (security restriction)
- User is not very experienced with git/command line — needs step-by-step guidance
- No `device_bash` available consistently — use `device_commit_files` for file sync
- Files must be copied to `/mnt/user-data/outputs/` before using `device_commit_files`
- **After device_commit_files, always stage files back and verify before giving git commands** (files silently fail sometimes)
- `db.create_all()` in `app/__init__.py` (line 46) creates tables on startup, conflicting with Alembic
- DEMO_MODE env var is `false` on Render; demo users detected by email pattern

## What's Been Built (this session)
1. **Realtime demo timestamp fix** — `_generate_intervals()` returns full timestamps; strip to HH:MM
2. **"All LOBs" aggregate** for Real-Time monitoring
3. **Scheduling inputs moved** from Settings to Scheduling page (Rotations, Accommodations, Availability, PTO links)
4. **RTM numeric reports** — 7 report tabs: Forecast/OTF, Interval, SVL Calculator, Absenteeism, Agent Status, Efficiency, Dashboard
5. **Actuals data model** — `IntervalActual` + `AgentStatusEvent` tables, CSV upload, Sheet reader, demo data
6. **Capacity Summary + Headcount tables** with CSV export
7. **PeopleWare API integration** — roster pull with skills/planning units, schedule import
8. **Rotation day-off enforcement** — engine skips employees when rotation says "off"
9. **Manual Add Shift** — modal with employee picker, template picker, auto-segments
10. **Vendor-neutral labels** — no company names hardcoded in UI
11. **Demo mode overhaul** — uses same rotation/PTO/accommodation/fill-in rules as real engine
12. **Google Sheets settings fix** — test unsaved form values, auto-test on save, friendly error messages, empty-message edge case

## Current State / What's In Progress

### Google Sheets Connection
- User is creating a **fresh Google Sheet** with tabs: EMPLOYEES, FORECAST RAW, REQUIREMENTS RAW
- Needs to share it with `serevo-sheets@serevo.iam.gserviceaccount.com`
- Needs to enable **Google Sheets API** and **Google Drive API** in the Google Cloud project "serevo" (console.cloud.google.com)
- Latest error fix (empty error message from gspread `SpreadsheetNotFound`) was synced to PC but may not be pushed yet
- Git commands to push:
  ```
  git add app/routes/settings.py
  git commit -m "Fix empty Google Sheets error message — detect SpreadsheetNotFound with no msg"
  git push origin main
  ```

### Pending / Not Yet Done
- **PeopleWare headcount pull**: Once API connection tests OK, user can Pull Employees
- **PeopleWare schedule import**: After headcount, test schedule import for a week
- **Actuals feed for RTM reports**: Pages show "No actuals loaded" until CSV upload, Sheet tabs, or API connector provides data

## Key Files (heavily modified this session)
- `app/demo_data.py` — demo schedule generation with rotation/PTO/accommodation rules
- `app/routes/scheduling.py` — generate, save, load, add-shift, PW import
- `app/scheduling/engine.py` — rotation day-off, fill-in rules
- `app/routes/settings.py` — Google Sheets test/save, PW connection, rotation generation
- `app/routes/realtime.py` — All LOBs aggregate, reports API
- `app/routes/capacity.py` — employee pull to DB, summary/headcount
- `app/capacity/planning.py` — PW legacy API, roster upsert, schedule import
- `app/realtime/actuals.py` — NEW: ACD interval stats and agent status
- `app/realtime/reports.py` — NEW: 7 report builders
- `app/templates/realtime/reports.html` — NEW: reports UI
- `app/templates/scheduling/index.html` — add shift modal, inputs bar, mode selector
- `app/templates/capacity/summary.html` — NEW: capacity summary page
- `app/models.py` — IntervalActual, AgentStatusEvent models
- `migrations/versions/a7d8e9f01b23_add_realtime_actuals.py` — NEW migration

## Technical Patterns
- Demo guard: `_demo_guard()` in CRUD routes, `DEMO_EMAILS` set for detection
- Segment types: `baseSegType()` JS normalizes "break1"/"break2" → "break" for colors
- Rotation: `_get_rotation_shift()` returns `{"off": True}` for day-off, template tuple for work day, `None` for no rotation
- Google Sheets: gspread + oauth2client ServiceAccountCredentials
- PW API: Bearer auth with token as-is, legacy API at `legacy-api.peopleware.com/v1/`
