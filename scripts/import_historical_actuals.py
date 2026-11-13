#!/usr/bin/env python3
"""Import historical actuals from CSV. Run on Render shell:
   python scripts/import_historical_actuals.py /path/to/file.csv
"""
import csv, sys, os, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_APP", "app")

from app import create_app
app = create_app()

def parse_time(date_str, time_str):
    date_str, time_str = date_str.strip(), time_str.strip()
    for fmt in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
        try: return datetime.datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError: pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError: pass
    return None

with app.app_context():
    from app.models import db, IntervalActual, PlanningUnit
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    pu_map = {pu.name.lower(): pu.id for pu in PlanningUnit.query.all()}
    print(f"Planning units: {sorted(pu_map.keys())}")

    csv_path = sys.argv[1]
    BATCH = 500
    batch, imported, skipped = [], 0, 0

    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lob = row["LOB"].strip()
            pu_id = pu_map.get(lob.lower())
            if not pu_id:
                skipped += 1; continue
            ts = parse_time(row["Date"].strip(), row["Timestamp"].strip())
            if not ts:
                skipped += 1; continue
            offered = int(float(row["Offered"].strip() or 0))
            aht_raw = row.get("AHT", "").strip()
            aht = float(aht_raw) if aht_raw else None
            batch.append({"planning_unit_id": pu_id, "timestamp": ts,
                "offered": offered, "answered": offered, "answered_within": offered,
                "abandoned": 0, "rolled": 0, "aht_secs": aht, "asa_secs": None,
                "route_id": "", "source": "upload", "max_queued": None})
            if len(batch) >= BATCH:
                stmt = pg_insert(IntervalActual.__table__).values(batch)
                stmt = stmt.on_conflict_do_update(constraint="uq_actual_unit_route_ts",
                    set_={"offered": stmt.excluded.offered, "answered": stmt.excluded.answered,
                           "answered_within": stmt.excluded.answered_within,
                           "aht_secs": stmt.excluded.aht_secs, "source": stmt.excluded.source})
                db.session.execute(stmt); db.session.commit()
                imported += len(batch); batch = []
                if imported % 10000 == 0: print(f"  {imported}...", flush=True)
    if batch:
        stmt = pg_insert(IntervalActual.__table__).values(batch)
        stmt = stmt.on_conflict_do_update(constraint="uq_actual_unit_route_ts",
            set_={"offered": stmt.excluded.offered, "answered": stmt.excluded.answered,
                   "answered_within": stmt.excluded.answered_within,
                   "aht_secs": stmt.excluded.aht_secs, "source": stmt.excluded.source})
        db.session.execute(stmt); db.session.commit()
        imported += len(batch)
    print(f"Done: {imported} imported, {skipped} skipped")
