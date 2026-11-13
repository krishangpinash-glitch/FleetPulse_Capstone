"""FleetPulse - Streamlit user interface. Run with:  streamlit run app.py"""
import logging
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import database as db
import services as sv

logging.basicConfig(filename=db.DB_PATH.parent / "fleetpulse.log", level=logging.INFO)
st.set_page_config(page_title="FleetPulse", page_icon="🚛", layout="wide")
conn = db.get_conn()
db.init_db(conn)

BADGE = {"Overdue": "🔴 Overdue", "Due soon": "🟠 Due soon", "Up to date": "🟢 Up to date", "Unknown": "⚪ Unknown"}
RANK = "Rank (1 = highest cost/km)"

# ---------------- sidebar: navigation, period, thresholds ----------------
st.sidebar.title("🚛 FleetPulse")
page = st.sidebar.radio("Go to", ["Dashboard", "Vehicles", "Fuel", "Mileage analytics",
                                  "Maintenance", "Cost per km", "Reports"])
with st.sidebar.expander("Reporting period and settings", expanded=True):
    start = st.date_input("From", date.today() - timedelta(days=365))
    end = st.date_input("To", date.today())
    soon_km = st.number_input("Due-soon threshold (km)", 0, 5000, 500, 50)
    soon_days = st.number_input("Due-soon threshold (days)", 0, 120, 15)
    min_dist = st.number_input("Minimum eligible distance to rank (km)", 0, 5000, 500, 50)
if start > end:
    st.sidebar.error("'From' must not be after 'To'.")
    st.stop()


# ---------------- helpers ----------------
def run(fn, ok_msg, *args, **kwargs):
    """Call a service function; show friendly messages instead of stack traces."""
    try:
        fn(conn, *args, **kwargs)
        st.success(ok_msg)
        return True
    except ValueError as e:
        st.error(str(e))
    except Exception:
        logging.exception("Unexpected error")
        st.error("Something went wrong and nothing was saved. Details are in data/fleetpulse.log.")
    return False


def vehicles_df(active_only=True):
    q = "SELECT * FROM vehicles" + (" WHERE is_active=1" if active_only else "") + " ORDER BY registration_number"
    return pd.read_sql_query(q, conn)


def service_df():
    rows = []
    for v in conn.execute("SELECT * FROM vehicles WHERE is_active=1 ORDER BY registration_number"):
        s = sv.service_status(dict(v), soon_km=soon_km, soon_days=soon_days)
        rows.append({"Vehicle": v["registration_number"], "Last service": v["last_service_date"] or "Unknown",
                     "Next service date": s["next_date"].isoformat() if s["next_date"] else "Unknown",
                     "Current odometer": v["current_odometer"],
                     "Next service odometer": s["next_odometer"] if s["next_odometer"] is not None else "Unknown",
                     "Remaining km": s["remaining_km"] if s["remaining_km"] is not None else "Unknown",
                     "Remaining days": s["remaining_days"] if s["remaining_days"] is not None else "Unknown",
                     "Status": BADGE[s["status"]], "Why": s["reason"], "Recommended action": s["action"]})
    return pd.DataFrame(rows)


def chart(fig, empty="No data for this chart in the selected period."):
    if fig is None:
        st.info(empty)
    else:
        st.plotly_chart(fig, use_container_width=True)


def reg_picker(label="Vehicle", active_only=True):
    regs = list(vehicles_df(active_only)["registration_number"])
    return st.selectbox(label, regs) if regs else None


perf = sv.performance_table(conn, start, end, min_dist)
st.sidebar.caption(f"Period: {start} to {end}. Dates are local calendar dates.")

# ---------------- Dashboard ----------------
if page == "Dashboard":
    st.title("Fleet dashboard")
    fuel = sv.fuel_df(conn, start, end)
    svc = service_df()
    valid = perf[perf["Eligible distance (km)"] > 0]
    c = st.columns(4)
    c[0].metric("Registered vehicles", len(vehicles_df()))
    c[1].metric("Vehicles needing service", int(svc["Status"].str.contains("Overdue|Due soon").sum()) if len(svc) else "No data")
    c[2].metric("Fuel spend", f"₹{fuel['total_cost'].sum():,.0f}" if len(fuel) else "No data")
    c[3].metric("Fuel consumed", f"{fuel['litres'].sum():,.0f} L" if len(fuel) else "No data")
    c = st.columns(3)
    avg = valid["Eligible distance (km)"].sum() / valid["Fuel for eligible distance (L)"].sum() if len(valid) else None
    c[0].metric("Fleet average mileage", f"{avg:.2f} km/L" if avg else "Not available")
    worst = perf[perf[RANK] == 1]
    c[1].metric("Highest fuel cost per km", f"₹{worst.iloc[0]['Cost per km (Rs)']:.2f}" if len(worst) else "Not available")
    c[2].metric("Worst-performing vehicle", worst.iloc[0]["Vehicle"] if len(worst) else "Not available")
    st.caption("Mileage uses the full-to-full method and is distance-weighted (total km / total litres).")

    ivs = sv.all_intervals(conn, start, end)
    left, right = st.columns(2)
    with left:
        st.subheader("Mileage by vehicle")
        d = valid.dropna(subset=["Avg mileage (km/L)"])
        chart(px.bar(d, x="Vehicle", y="Avg mileage (km/L)", color="Type") if len(d) else None)
        st.subheader("Monthly fuel expenditure")
        if len(fuel):
            m = fuel.assign(Month=fuel["fill_date"].str[:7]).groupby("Month", as_index=False)["total_cost"].sum()
            chart(px.bar(m, x="Month", y="total_cost", labels={"total_cost": "Fuel spend (₹)"}))
        else:
            chart(None)
        st.subheader("Mileage trend")
        chart(px.line(ivs, x="fill_date", y="km_per_l", color="Vehicle", markers=True,
                      labels={"km_per_l": "km/L", "fill_date": "Date"}) if len(ivs) else None)
    with right:
        st.subheader("Fuel spend by vehicle")
        chart(px.bar(perf[perf["Total fuel spend (Rs)"] > 0], x="Vehicle", y="Total fuel spend (Rs)") if len(fuel) else None)
        st.subheader("Service status")
        if len(svc):
            counts = svc["Status"].value_counts().reset_index()
            chart(px.bar(counts, x="Status", y="count", labels={"count": "Vehicles"}))
        else:
            chart(None)
        st.subheader("Fuel cost per km")
        r = perf.dropna(subset=["Cost per km (Rs)"])
        chart(px.bar(r, x="Vehicle", y="Cost per km (Rs)", color="Type") if len(r) else None)

# ---------------- Vehicles ----------------
elif page == "Vehicles":
    st.title("Vehicle management")
    tab_list, tab_add, tab_edit = st.tabs(["Registry", "Register vehicle", "Update / deactivate"])
    with tab_list:
        show_all = st.checkbox("Include inactive vehicles")
        v = vehicles_df(not show_all).drop(columns=["id", "created_at", "updated_at"])
        st.dataframe(v, use_container_width=True) if len(v) else st.info("No vehicles registered yet.")
    with tab_add:
        with st.form("add_vehicle", clear_on_submit=True):
            reg = st.text_input("Registration number")
            vtype = st.selectbox("Vehicle type", sv.VEHICLE_TYPES)
            odo = st.number_input("Current odometer (km)", min_value=0.0, step=100.0)
            reg_date = st.date_input("Registration date", date.today() - timedelta(days=365))
            has_service = st.checkbox("Service history available", value=True)
            last_date = st.date_input("Last service date", date.today() - timedelta(days=30))
            last_odo = st.number_input("Odometer at last service (km)", min_value=0.0, step=100.0)
            km_int = st.number_input("Service interval (km)", min_value=0.0, step=500.0, value=10000.0)
            mo_int = st.number_input("Service interval (months)", min_value=0, step=1, value=6)
            if st.form_submit_button("Register vehicle"):
                run(sv.add_vehicle, "Vehicle registered.", reg, vtype, odo, reg_date,
                    last_date if has_service else None, last_odo if has_service else None,
                    km_int or None, mo_int or None)
    with tab_edit:
        regs = vehicles_df(False)
        if regs.empty:
            st.info("No vehicles yet.")
        else:
            pick = st.selectbox("Vehicle", regs["registration_number"], key="edit_pick")
            row = regs[regs["registration_number"] == pick].iloc[0]
            with st.form("edit_vehicle"):
                t = st.selectbox("Vehicle type", sv.VEHICLE_TYPES, index=sv.VEHICLE_TYPES.index(row["vehicle_type"]))
                o = st.number_input("Current odometer (km)", min_value=0.0, value=float(row["current_odometer"]), step=100.0)
                k = st.number_input("Service interval (km)", min_value=0.0, value=float(row["service_interval_km"] or 0), step=500.0)
                m = st.number_input("Service interval (months)", min_value=0, value=int(row["service_interval_months"] or 0))
                if st.form_submit_button("Save changes"):
                    run(sv.update_vehicle, "Vehicle updated.", int(row["id"]), t, o, k or None, m or None)
            active = bool(row["is_active"])
            if st.button("Deactivate vehicle" if active else "Reactivate vehicle"):
                run(sv.set_vehicle_active, "Status changed (history is kept).", int(row["id"]), not active)

# ---------------- Fuel ----------------
elif page == "Fuel":
    st.title("Fuel entry and history")
    tab_add, tab_hist = st.tabs(["Record a fill", "Fuel history"])
    with tab_add:
        regs = list(vehicles_df()["registration_number"])
        if not regs:
            st.info("Register a vehicle first.")
        else:
            with st.form("add_fuel", clear_on_submit=True):
                reg = st.selectbox("Vehicle", regs)
                d = st.date_input("Fill date", date.today())
                odo = st.number_input("Odometer at refuelling (km)", min_value=0.0, step=10.0)
                litres = st.number_input("Fuel quantity (litres)", min_value=0.0, step=1.0)
                cost = st.number_input("Total cost (₹)", min_value=0.0, step=50.0)
                full = st.checkbox("Tank filled completely (full fill)", value=True)
                notes = st.text_input("Notes (optional)")
                if st.form_submit_button("Save fill"):
                    run(sv.add_fuel, "Fuel fill saved.", reg, d, odo, litres, cost, full, notes)
            st.caption("Tick 'full fill' only if the tank was filled to the brim. Partial fills are counted in the "
                       "litres but are never used as a starting or ending point for mileage.")
    with tab_hist:
        reg = reg_picker("Vehicle", active_only=False)
        if reg:
            vid = conn.execute("SELECT id FROM vehicles WHERE registration_number=?", (reg,)).fetchone()["id"]
            recs = sv.fuel_records_for(conn, vid)
            m = {(i["fill_date"], i["odometer_reading"]): i["km_per_l"] for i in sv.mileage_intervals(recs)}
            for r in recs:
                key = (r["fill_date"], r["odometer_reading"])
                r["Mileage (km/L)"] = f"{m[key]:.2f}" if key in m else ("Partial fill" if not r["is_full_fill"] else "Not available")
            st.dataframe(pd.DataFrame(recs), use_container_width=True) if recs else st.info("No fuel records for this vehicle.")

# ---------------- Mileage analytics ----------------
elif page == "Mileage analytics":
    st.title("Mileage analytics")
    regs = list(vehicles_df()["registration_number"])
    chosen = st.multiselect("Vehicles (empty = all)", regs)
    ivs = sv.all_intervals(conn, start, end, chosen or None)
    table = perf[perf["Vehicle"].isin(chosen)] if chosen else perf
    with st.expander("How is average mileage calculated?"):
        st.write("**Average mileage = total eligible distance ÷ total litres used over those intervals.** "
                 "Each interval runs from one full fill to the next. This is not the simple mean of per-fill "
                 "km/L, because a short interval would count as much as a long one. First fills, partial-fill "
                 "reference points and intervals with zero or negative distance are excluded.")
    if ivs.empty:
        st.info("No eligible mileage intervals for this selection and period.")
    else:
        c1, c2 = st.columns(2)
        best = table.dropna(subset=["Avg mileage (km/L)"])
        c1.metric("Best mileage", f"{best.loc[best['Avg mileage (km/L)'].idxmax(), 'Vehicle']} ({best['Avg mileage (km/L)'].max():.2f} km/L)")
        c2.metric("Worst mileage", f"{best.loc[best['Avg mileage (km/L)'].idxmin(), 'Vehicle']} ({best['Avg mileage (km/L)'].min():.2f} km/L)")
        st.dataframe(table.drop(columns=[RANK]).round(2), use_container_width=True)
        chart(px.bar(best, x="Vehicle", y="Avg mileage (km/L)", color="Type", title="Average km/L by vehicle"))
        chart(px.line(ivs, x="fill_date", y="km_per_l", color="Vehicle", markers=True, title="Mileage trend"))
        chart(px.bar(table, x="Vehicle", y="Total fuel spend (Rs)", title="Fuel expenditure"))

# ---------------- Maintenance ----------------
elif page == "Maintenance":
    st.title("Maintenance")
    tab_sched, tab_rec, tab_hist = st.tabs(["Schedule and alerts", "Record service", "Service history"])
    with tab_sched:
        svc = service_df()
        st.dataframe(svc, use_container_width=True) if len(svc) else st.info("No active vehicles.")
        st.caption("A vehicle is Overdue if either the km or the date limit is passed; Due soon if either is within "
                   "the thresholds in the sidebar. Missing service details show Unknown, never 'Up to date'.")
    with tab_rec:
        regs = list(vehicles_df()["registration_number"])
        if regs:
            with st.form("rec_service", clear_on_submit=True):
                reg = st.selectbox("Vehicle", regs)
                d = st.date_input("Service date", date.today())
                odo = st.number_input("Odometer at service (km)", min_value=0.0, step=100.0)
                kind = st.text_input("Service type", "General service")
                desc = st.text_input("Description (optional)")
                cost = st.number_input("Cost (₹)", min_value=0.0, step=100.0)
                if st.form_submit_button("Save service"):
                    run(sv.record_maintenance, "Service recorded and schedule updated.", reg, d, odo, kind, desc, cost)
        else:
            st.info("Register a vehicle first.")
    with tab_hist:
        h = pd.read_sql_query("SELECT v.registration_number AS Vehicle, m.service_date, m.odometer_reading, "
                              "m.service_type, m.description, m.cost FROM maintenance_records m "
                              "JOIN vehicles v ON v.id=m.vehicle_id ORDER BY m.service_date DESC", conn)
        st.dataframe(h, use_container_width=True) if len(h) else st.info("No service records yet.")

# ---------------- Cost per km ----------------
elif page == "Cost per km":
    st.title("Cost per kilometre and worst performer")
    st.write(f"**Metric:** fuel cost per km = eligible fuel cost ÷ eligible distance. **Period:** {start} to {end}. "
             f"**Minimum distance to rank:** {min_dist:g} km.")
    ranked = perf[perf["Data status"] == "Ranked"].sort_values(RANK)
    if ranked.empty:
        st.info("No vehicle has enough valid data to rank in this period.")
    else:
        w = ranked.iloc[0]
        st.error(f"Worst performer: **{w['Vehicle']}** at ₹{w['Cost per km (Rs)']:.2f} per km "
                 f"({w['Avg mileage (km/L)']:.2f} km/L).")
        st.dataframe(ranked.drop(columns=["Data status"]).round(2), use_container_width=True)
        chart(px.bar(ranked, x="Vehicle", y="Cost per km (Rs)", color="Type"))
    excluded = perf[perf["Data status"] != "Ranked"][["Vehicle", "Type", "Data status"]]
    if len(excluded):
        st.subheader("Not ranked")
        st.dataframe(excluded, use_container_width=True)
    st.caption("Limitation: only complete full-to-full intervals ending inside the period are used, so fuel bought "
               "near the period boundary may fall outside the window.")
    reg = reg_picker("Inspect a vehicle's records")
    if reg:
        iv = sv.all_intervals(conn, start, end, [reg])
        st.dataframe(iv.round(2), use_container_width=True) if len(iv) else st.info("No eligible intervals.")

# ---------------- Reports ----------------
elif page == "Reports":
    st.title("Reports and data export")
    st.caption(f"Generated {date.today()}. Period {start} to {end}. Exports use the same calculations as the dashboard.")
    exports = {
        "vehicle_registry.csv": vehicles_df(False),
        "fuel_history.csv": sv.fuel_df(conn, start, end, include_inactive=True),
        "mileage_analytics.csv": sv.all_intervals(conn, start, end),
        "maintenance_due.csv": service_df(),
        "maintenance_history.csv": pd.read_sql_query(
            "SELECT v.registration_number, m.* FROM maintenance_records m JOIN vehicles v ON v.id=m.vehicle_id", conn),
        "vehicle_performance_ranking.csv": perf,
    }
    for name, df in exports.items():
        st.download_button(f"Download {name} ({len(df)} rows)", df.to_csv(index=False), file_name=name, mime="text/csv")
