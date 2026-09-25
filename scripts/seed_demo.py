"""
seed_demo.py  —  fill the demo Google Sheets with realistic synthetic data
==========================================================================

Run once (or any time you want to reset the demo):

    export $(cat .env | xargs)      # load your settings (Mac/Linux)
    python seed_demo.py

What it writes (all fake — purely synthetic demo data):
  Capacity sheet (CAPACITY_SHEET_KEY):
      EMPLOYEES         - roster with skills
      REQUIREMENTS RAW  - 30-min agents_required, by LOB, ~14 days (long format)
      FORECAST RAW      - 30-min offered + aht, by LOB, ~14 days (long format)
  Main sheet (SHEET_KEY):
      sheet1            - tickets across statuses & dates
      LOA               - fictional leave records
      ACCOMMODATIONS    - fictional accommodation records
  Roster sheet (ROSTER_SHEET_ID, falls back to SHEET_KEY):
      Schedule          - RTM roster with shift times

Every column header below matches what the app reads. Adjust the CONFIG block
if your tab names or which-sheet-holds-what differ.
"""

import os, random, datetime

random.seed(11)

# ------------------------------------------------------------------ CONFIG
LOBS      = ["Sales EN", "Sales FR", "Support", "Billing"]
INTERVAL_MIN = 30
OPEN_HOUR, CLOSE_HOUR = 8, 21          # business hours for intraday rows
DAYS_BACK, DAYS_FWD   = 7, 7           # history + forecast horizon
N_EMPLOYEES = 40
N_TICKETS   = 250

FIRST = ["Alex","Sam","Jordan","Taylor","Casey","Riley","Morgan","Jamie","Avery",
         "Quinn","Cameron","Drew","Reese","Skyler","Parker","Rowan","Emerson",
         "Finley","Hayden","Kai","Logan","Micah","Noel","Sage","Blair","Devon",
         "Elliot","Frankie","Gray","Harper","Indi","Jules","Kris","Lane","Marlo",
         "Nico","Oakley","Perry","Robin","Shay"]
LAST  = ["Rivera","Chen","Lee","Patel","Nguyen","Garcia","Kim","Silva","Haddad",
         "Okafor","Brooks","Costa","Duval","Ellis","Ford","Greer","Holt","Ives",
         "Jansen","Kowal","Lund","Mora","Novak","Ortiz","Pace","Rao","Sato",
         "Tran","Ueda","Vega","Ward","Yost","Zane","Abbot","Bello","Cruz",
         "Diaz","Estes","Frost","Gill"]

TICKET_STATUSES = ["Open", "In Progress", "Pending", "Closed", "Closed", "Closed"]
WFM_REQUESTS = ["Schedule Change", "Shift Swap", "Time Off", "Overtime",
                "Break Adjustment", "Skill Change", "Adherence Query"]
TEAM_LEADS = ["Demo Lead A", "Demo Lead B", "Demo Lead C"]

# LOA / accommodations — GENERIC, non-medical reasons only
LOA_REASONS   = ["Vacation", "Personal Leave", "Bereavement", "Jury Duty", "Parental"]
ACCOM_TYPES   = ["Personal", "Business Needs", "Other"]        # deliberately not "Medical"
CONTRACTS     = ["FT 40hrs 5x8hr", "PT 32hrs 4x8hr", "PT 24hrs 3x8hr"]

# tab names / headers (match the app)
TAB_EMP, TAB_REQ, TAB_FC = "EMPLOYEES", "REQUIREMENTS RAW", "FORECAST RAW"
EMP_HEADERS = ["Status","First Name","Last Name","Employee ID","Latest Skill Name",
               "Latest Skill Start","Latest Skill End","All Skills","End Date"]
REQ_HEADERS = ["Timestamp","LOB","agents_required"]
FC_HEADERS  = ["Timestamp","LOB","offered","aht"]

TICKET_HEADERS = ["Ticket ID","Status","advisor_name","request_date","team_lead",
                  "wfm_request","Submitted By","Submitted At","Assigned To",
                  "Closed At","Closed By","Notes"]

LOA_TAB, ACCOM_TAB = "LOA", "ACCOMMODATIONS"
LOA_HEADERS = ["id","lob","name","team_lead","loa_reason","start_date","return_date",
               "ems_status","returned","status","archived","notes","created_at","updated_at"]
ACCOM_HEADERS = ["id","lob","name","monday","tuesday","wednesday","thursday","friday",
                 "saturday","sunday","request_date","start_date","end_date",
                 "injixo_contract","status","accommodation_type","comments",
                 "archived","created_at","updated_at"]

SCHED_TAB = "Schedule"
SCHED_HEADERS = ["Current ID","First Name","Last Name","Status","Employee Name",
                 "Start Time","End Time"]

# ------------------------------------------------------------------ helpers
def gc_client():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        os.environ.get("SERVICE_ACCOUNT_FILE", "service_account.json"), scope)
    return gspread.authorize(creds)

def open_sheet(client, env_key, fallback_env=None):
    key = os.environ.get(env_key, "") or (os.environ.get(fallback_env, "") if fallback_env else "")
    if not key:
        raise SystemExit(f"Missing sheet key: set {env_key} in your .env")
    return client.open_by_key(key)

def write_tab(sheet, tab, headers, rows):
    """Create-or-clear the tab, then write header + rows in one batch."""
    try:
        ws = sheet.worksheet(tab)
        ws.clear()
    except Exception:
        ws = sheet.add_worksheet(title=tab, rows=str(len(rows) + 10), cols=str(len(headers) + 2))
    ws.update([headers] + rows, value_input_option="RAW")
    print(f"  wrote {len(rows):>4} rows -> {tab}")

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def intraday_weight(hour, weekend):
    # bimodal-ish curve: lunch + evening peaks
    import math
    lunch = math.exp(-0.5*((hour-12)/2.0)**2)
    eve   = math.exp(-0.5*((hour-18)/2.2)**2) * (1.1 if weekend else 0.9)
    return max(lunch + eve, 0.05)

# ------------------------------------------------------------------ builders
def build_employees():
    rows, people = [], []
    for i in range(N_EMPLOYEES):
        fn, ln = random.choice(FIRST), random.choice(LAST)
        emp_id = f"E{1000+i}"
        skill = random.choice(LOBS)
        people.append((emp_id, fn, ln, skill))
        rows.append(["Active", fn, ln, emp_id, skill,
                     "2024-01-01", "2999-12-31",
                     ", ".join(random.sample(LOBS, k=random.randint(1, len(LOBS)))),
                     ""])
    return rows, people

def build_intervals():
    req_rows, fc_rows = [], []
    today = datetime.date.today()
    start = today - datetime.timedelta(days=DAYS_BACK)
    for d in range(DAYS_BACK + DAYS_FWD):
        day = start + datetime.timedelta(days=d)
        weekend = day.weekday() >= 5
        for lob in LOBS:
            base = {"Sales EN": 9, "Sales FR": 5, "Support": 12, "Billing": 6}.get(lob, 7)
            h = OPEN_HOUR
            while h < CLOSE_HOUR:
                for m in (0, 30):
                    ts = f"{day.strftime('%Y-%m-%d')} {h:02d}:{m:02d}:00"
                    w = intraday_weight(h + m/60.0, weekend)
                    offered = round(base * w * random.uniform(0.85, 1.15) * 6, 1)
                    aht = round(random.uniform(240, 360), 0)
                    agents = max(1, round(offered * (aht/1800) / 0.85 + 1))
                    req_rows.append([ts, lob, agents])
                    fc_rows.append([ts, lob, offered, aht])
                h += 1
    return req_rows, fc_rows

def build_tickets(people):
    rows = []
    today = datetime.date.today()
    for i in range(N_TICKETS):
        emp_id, fn, ln, _ = random.choice(people)
        status = random.choice(TICKET_STATUSES)
        sub = today - datetime.timedelta(days=random.randint(0, 21),
                                         hours=random.randint(0, 8))
        submitted = sub.strftime("%Y-%m-%d %H:%M:%S")
        closed = ""
        closed_by = ""
        if status == "Closed":
            cl = sub + datetime.timedelta(hours=random.randint(1, 40))
            closed = cl.strftime("%Y-%m-%d %H:%M:%S")
            closed_by = "Demo Admin"
        rows.append([
            f"TKT-{2000+i}", status, f"{fn} {ln}", sub.strftime("%Y-%m-%d"),
            random.choice(TEAM_LEADS), random.choice(WFM_REQUESTS),
            f"{fn} {ln}", submitted,
            "Demo Admin" if status != "Open" else "",
            closed, closed_by, ""
        ])
    return rows

def build_loa(people):
    rows = []
    today = datetime.date.today()
    for i in range(8):
        emp_id, fn, ln, lob = random.choice(people)
        start = today + datetime.timedelta(days=random.randint(-10, 20))
        ret = start + datetime.timedelta(days=random.randint(3, 21))
        rows.append([f"LOA{i:03d}", lob, f"{fn} {ln}", random.choice(TEAM_LEADS),
                     random.choice(LOA_REASONS), start.strftime("%Y-%m-%d"),
                     ret.strftime("%Y-%m-%d"), "N/A", "No", "Active", "No",
                     "", now_str(), now_str()])
    return rows

def build_accom(people):
    rows = []
    today = datetime.date.today()
    for i in range(6):
        emp_id, fn, ln, lob = random.choice(people)
        days = ["Yes" if random.random() < 0.6 else "" for _ in range(7)]
        rows.append([f"ACC{i:03d}", lob, f"{fn} {ln}"] + days +
                    [today.strftime("%Y-%m-%d"),
                     today.strftime("%Y-%m-%d"),
                     (today + datetime.timedelta(days=90)).strftime("%Y-%m-%d"),
                     random.choice(CONTRACTS), "Active",
                     random.choice(ACCOM_TYPES), "Demo record", "No",
                     now_str(), now_str()])
    return rows

def build_schedule(people):
    rows = []
    for emp_id, fn, ln, _ in people:
        start_h = random.choice([8, 9, 10, 11, 12])
        end_h = start_h + 8
        rows.append([emp_id, fn, ln, "Scheduled", f"{ln}, {fn}",
                     f"{start_h:02d}:00", f"{min(end_h,21):02d}:00"])
    return rows

# ------------------------------------------------------------------ main
def main():
    client = gc_client()
    cap    = open_sheet(client, "CAPACITY_SHEET_KEY")
    main_s = open_sheet(client, "SHEET_KEY")
    roster = open_sheet(client, "ROSTER_SHEET_ID", fallback_env="SHEET_KEY")

    print("Seeding capacity sheet…")
    emp_rows, people = build_employees()
    write_tab(cap, TAB_EMP, EMP_HEADERS, emp_rows)
    req_rows, fc_rows = build_intervals()
    write_tab(cap, TAB_REQ, REQ_HEADERS, req_rows)
    write_tab(cap, TAB_FC,  FC_HEADERS,  fc_rows)

    print("Seeding main sheet…")
    # tickets go on sheet1 (the app's default worksheet)
    ws1 = main_s.sheet1
    ws1.clear()
    ws1.update([TICKET_HEADERS] + build_tickets(people), value_input_option="RAW")
    print(f"  wrote {N_TICKETS:>4} rows -> sheet1 (tickets)")
    write_tab(main_s, LOA_TAB,   LOA_HEADERS,   build_loa(people))
    write_tab(main_s, ACCOM_TAB, ACCOM_HEADERS, build_accom(people))

    print("Seeding roster sheet…")
    write_tab(roster, SCHED_TAB, SCHED_HEADERS, build_schedule(people))

    print("\nDone. Open your demo Sheets — they now hold synthetic data only.")

if __name__ == "__main__":
    main()
