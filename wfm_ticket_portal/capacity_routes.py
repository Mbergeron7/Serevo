# ========== CAPACITY PLANNING ==========
# Add this import near the top of wfm_ticket_portal.py:
# import capacity_planning as cp

@app.route('/capacity')
def capacity_panel():
    auth = _require_auth()
    if auth: return auth
    try:
        api_status = cp.test_connection()
    except Exception:
        api_status = {"ok": False, "legacy": {"ok": False, "status": 0}, 
                      "new_api": {"ok": False, "status": 0}}

    # Sheet data status
    status = {"forecast_rows": 0, "req_rows": 0, "emp_count": 0,
              "forecast_updated": None, "req_updated": None, "emp_updated": None}
    try:
        fc_ws  = main_doc.worksheet("FORECAST RAW")
        fc_all = fc_ws.get_all_values()
        status["forecast_rows"] = max(0, len(fc_all) - 3)  # minus header rows
        if len(fc_all) > 4:
            status["forecast_updated"] = fc_all[3][1] if len(fc_all[3]) > 1 else None
    except Exception:
        pass
    try:
        rq_ws  = main_doc.worksheet("REQUIREMENTS RAW")
        rq_all = rq_ws.get_all_values()
        status["req_rows"] = max(0, len(rq_all) - 3)
        if len(rq_all) > 4:
            status["req_updated"] = rq_all[3][1] if len(rq_all[3]) > 1 else None
    except Exception:
        pass
    try:
        em_ws  = main_doc.worksheet("EMPLOYEES")
        em_all = em_ws.get_all_values()
        status["emp_count"] = max(0, len(em_all) - 1)
        if len(em_all) > 1:
            status["emp_updated"] = "on file"
    except Exception:
        pass

    return render_template('capacity_panel.html',
        api_status=api_status,
        status=status,
        workloads=sorted(cp.WORKLOADS.keys()),
        current_year=datetime.now(ZoneInfo(TIMEZONE)).year,
    )


@app.route('/capacity/test_connection')
def capacity_test_connection():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        result = cp.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/capacity/refresh', methods=['POST'])
def capacity_refresh():
    auth = _require_auth()
    if auth: return jsonify({"error": "Not authenticated"}), 403
    try:
        payload    = request.get_json(silent=True) or {}
        pull_type  = payload.get("type", "forecast")
        year       = int(payload.get("year", datetime.now(ZoneInfo(TIMEZONE)).year))
        start_str  = payload.get("start_date", f"{year}-01-01")
        end_str    = payload.get("end_date",   f"{year}-12-31")
        utc_offset = int(payload.get("utc_offset", -4))
        append     = payload.get("append", True)

        start_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_dt   = datetime.strptime(end_str,   "%Y-%m-%d").date()
        all_days = [(start_dt + timedelta(days=i))
                    for i in range((end_dt - start_dt).days + 1)]

        import time
        t0           = time.time()
        rows_written = 0
        skipped      = 0

        if pull_type in ("forecast", "all"):
            try:
                fc_ws = main_doc.worksheet("FORECAST RAW")
            except Exception:
                fc_ws = main_doc.add_worksheet("FORECAST RAW", rows="15000", cols="50")
                fc_ws.update("A1", [["FORECAST RAW — Offered Calls by Workload (30-min intervals)"]])
                fc_ws.update("B3", [["Timestamp"]])

            if not append:
                # Clear data rows (keep header rows 1-3)
                try:
                    all_vals = fc_ws.get_all_values()
                    if len(all_vals) > 3:
                        fc_ws.delete_rows(4, len(all_vals))
                except Exception:
                    pass

            for lob_name, wid in cp.WORKLOADS.items():
                for day in all_days:
                    intervals = cp.fetch_forecast_for_day(lob_name, wid, day, utc_offset)
                    if intervals:
                        written = cp.write_forecast_to_sheet(fc_ws, lob_name, intervals)
                        rows_written += written
                    else:
                        skipped += 1

        if pull_type in ("requirements", "all"):
            try:
                rq_ws = main_doc.worksheet("REQUIREMENTS RAW")
            except Exception:
                rq_ws = main_doc.add_worksheet("REQUIREMENTS RAW", rows="20000", cols="60")
                rq_ws.update("A1", [["REQUIREMENTS RAW — Agent Requirements by Planning Unit (30-min intervals)"]])
                rq_ws.update("B3", [["Timestamp"]])

            if not append:
                try:
                    all_vals = rq_ws.get_all_values()
                    if len(all_vals) > 3:
                        rq_ws.delete_rows(4, len(all_vals))
                except Exception:
                    pass

            planning_units = cp.fetch_planning_units()
            for pu in planning_units:
                pu_id   = pu.get("planning_unit_id") or pu.get("id", "")
                pu_name = pu.get("name", "")
                if not pu_id or cp._lob_to_planning_unit(pu_name) == pu_name and pu_name not in cp.WORKLOADS:
                    pass  # include all PUs
                for day in all_days:
                    day_reqs = cp.fetch_requirements_for_day(pu_id, pu_name, day)
                    if day_reqs:
                        written = cp.write_requirements_to_sheet(rq_ws, day_reqs)
                        rows_written += written
                    else:
                        skipped += 1

        if pull_type in ("employees", "all"):
            try:
                em_ws = main_doc.worksheet("EMPLOYEES")
            except Exception:
                em_ws = main_doc.add_worksheet("EMPLOYEES", rows="1000", cols="25")

            employees = cp.fetch_employees()
            if employees:
                written = cp.write_employees_to_sheet(em_ws, employees)
                rows_written += written
            else:
                skipped += 1

        duration = f"{int(time.time() - t0)}s"
        return jsonify({"success": True, "rows_written": rows_written,
                        "skipped": skipped, "duration": duration})

    except Exception as e:
        log.exception("Capacity refresh error")
        return jsonify({"success": False, "error": str(e)})


@app.route('/capacity/plan')
def capacity_plan_view():
    auth = _require_auth()
    if auth: return auth
    try:
        year = int(request.args.get("year",
                   datetime.now(ZoneInfo(TIMEZONE)).year))
        years = list(range(2024, datetime.now(ZoneInfo(TIMEZONE)).year + 2))

        fc_ws = rq_ws = em_ws = None
        try: fc_ws = main_doc.worksheet("FORECAST RAW")
        except Exception: pass
        try: rq_ws = main_doc.worksheet("REQUIREMENTS RAW")
        except Exception: pass
        try: em_ws = main_doc.worksheet("EMPLOYEES")
        except Exception: pass

        plan = []
        if fc_ws or rq_ws:
            plan = cp.compute_capacity_plan(fc_ws, rq_ws, em_ws, year)

        now_str = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M")
        return render_template('capacity_plan.html',
            plan=plan, year=year, years=years, now=now_str)

    except Exception as e:
        log.exception("Capacity plan view error")
        return f"Capacity plan error: {str(e)}", 500
