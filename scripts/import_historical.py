#!/usr/bin/env python3
"""Import historical interval actuals from CSV into the database.

CSV format: LOB, Date, Timestamp, Offered, AHT
Source: source='historical_import' so it won't be wiped by sheet resync.

Usage:
    python scripts/import_historical.py path/to/file.csv
"""
import csv
import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_APP", "app")

from app import create_app

app = create_app()


def parse_time(date_str, time_str):
    """Parse date + time into a datetime.  Handles 12h (AM/PM) and 24h formats."""
    date_str = date_str.strip()
    time_str = time_str.strip()
    # Try 12h format first
    for fmt in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            pass
    # 24h format
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            pass
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_historical.py <csv_file>")
        sys.exit(1)

    csv_path = sys.argv[1]
    with app.app_context():
        from app.models import db, IntervalActual, PlanningUnit

        # Build LOB -> PU ID mapping
        pu_map = {}
        for pu in PlanningUnit.query.all():
            pu_map[pu.name.lower()] = pu.id
        print(f"Planning units: {list(pu_map.keys())}")

        # Read CSV
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            batch = []
            imported = 0
            skipped = 0
            no_pu = set()

            for row in reader:
                lob = row["LOB"].strip()
                pu_id = pu_map.get(lob.lower())
                if not pu_id:
                    if lob not in no_pu:
                        no_pu.add(lob)
                    skipped += 1
                    continue

                ts = parse_time(row["Date"].strip(), row["Timestamp"].strip())
                if not ts:
                    skipped += 1
                    continue

                offered = int(float(row["Offered"].strip() or 0))
                aht_raw = row.get("AHT", "").strip()
                aht = float(aht_raw) if aht_raw else None

                batch.append({
                    "planning_unit_id": pu_id,
                    "timestamp": ts,
                    "offered": offered,
                    "answered": offered,  # historical data: assume all offered were answered
                    "answered_within": offered,  # no SL breakdown available
                    "abandoned": 0,
                    "rolled": 0,
                    "aht_secs": aht,
                    "asa_secs": None,
                    "route_id": None,
                    "source": "historical_import",
                })

                if len(batch) >= 500:
                    _upsert_batch(db, IntervalActual, batch)
                    imported += len(batch)
                    print(f"  imported {imported}...", flush=True)
                    batch = []

            if batch:
                _upsert_batch(db, IntervalActual, batch)
                imported += len(batch)

            db.session.commit()
            print(f"\nDone: {imported} records imported, {skipped} skipped")
            if no_pu:
                print(f"LOBs not found as planning units: {sorted(no_pu)}")


def _upsert_batch(db, IntervalActual, batch):
    for rec in batch:
        existing = IntervalActual.query.filter_by(
            planning_unit_id=rec["planning_unit_id"],
            route_id=rec["route_id"],
            timestamp=rec["timestamp"],
        ).first()
        if existing:
            # Only update if existing has no real data (was empty/zero)
            if (existing.offered or 0) == 0 and rec["offered"] > 0:
                for k, v in rec.items():
                    setattr(existing, k, v)
        else:
            db.session.add(IntervalActual(**rec))
    db.session.flush()


if __name__ == "__main__":
    main()
