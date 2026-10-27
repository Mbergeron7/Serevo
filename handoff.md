# Serevo WFM Platform — Session Handoff

## Project Overview
Serevo is a freelance WFM (Workforce Management) web platform Mike is building. It's a Flask + SQLAlchemy app deployed on Render.

- **Device path**: `C:\Users\mikeb\OneDrive\Freelance Project\Serevo`
- **GitHub repo**: `github.com/Mbergeron7/Serevo`
- **Render deploy**: auto-deploys from `main` branch
- **Render Build Command**: `pip install -r requirements.txt && flask db upgrade`
- **Demo emails**: `{"demo@serevo.app", "admin@demo.serevo.app", "viewer@demo.serevo.app"}`
- **Service account email**: `serevo-sheets@serevo.iam.gserviceaccount.com`

## Standing Constraints (preserve always)
- **No client-specific references anywhere** — fully white-labeled, all generic
- **No vendor names in UI** — use "Import from API" / "connected system"
- **Git push commands must be provided every time files are synced to PC**
- **.env file cannot be written via device_commit_files** (security restriction)
- User is not very experienced with git/command line — needs step-by-step guidance
- No `device_bash` available consistently — use `device_commit_files` for file sync
- Files must be copied to `/mnt/user-data/outputs/` before using `device_commit_files`
- **After device_commit_files, always stage files back and verify before giving git commands** (files silently fail sometimes)
- DEMO_MODE env var is `false` on Render; demo users detected by email pattern

## What's Been Built
1. **Realtime demo timestamp fix** — `_generate_intervals()` returns full timestamps; strip to HH:MM
2. **"All LOBs" aggregate** for Real-Time monitoring
3. **Scheduling inputs moved** from Settings to Scheduling page (Rotations, Accommodations, Availability, PTO links)
4. **RTM numeric reports** — 7 report tabs: Forecast/OTF, Interval, SVL Calculator, Absenteeism, Agent Status, Efficiency, Dashboard
5. **Actuals data model** — `IntervalActual` + `AgentStatusEvent` tables, CSV upload, Sheet reader, demo data
6. **Capacity Summary + Headcount tables** with CSV export
7. **WFM API integration** — roster pull with skills/planning units, schedule import
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

### Pending / Not Yet Done
- **Employee headcount pull**: Once API connection tests OK, user can Pull Employees
- **Schedule import**: After headcount, test schedule import for a week
- **Actuals feed for RTM reports**: Pages show "No actuals loaded" until CSV upload, Sheet tabs, or API connector provides data

## Key Files (heavily modified)
- `app/demo_data.py` — demo schedule generation with rotation/PTO/accommodation rules
- `app/routes/scheduling.py` — generate, save, load, add-shift, API import
- `app/scheduling/engine.py` — rotation day-off, fill-in rules
- `app/routes/settings.py` — Google Sheets test/save, API connection, rotation generation
- `app/routes/realtime.py` — All LOBs aggregate, reports API
- `app/routes/capacity.py` — employee pull to DB, summary/headcount
- `app/capacity/planning.py` — legacy API, roster upsert, schedule import
- `app/realtime/actuals.py` — ACD interval stats and agent status
- `app/realtime/reports.py` — 7 report builders
- `app/templates/realtime/reports.html` — reports UI
- `app/templates/scheduling/index.html` — add shift modal, inputs bar, mode selector
- `app/templates/capacity/summary.html` — capacity summary page
- `app/models.py` — IntervalActual, AgentStatusEvent models

## Technical Patterns
- Demo guard: `_demo_guard()` in CRUD routes, `DEMO_EMAILS` set for detection
- Segment types: `baseSegType()` JS normalizes "break1"/"break2" → "break" for colors
- Rotation: `_get_rotation_shift()` returns `{"off": True}` for day-off, template tuple for work day, `None` for no rotation
- Google Sheets: gspread + oauth2client ServiceAccountCredentials
- WFM API: Bearer auth with token, legacy API endpoint configured via env vars
