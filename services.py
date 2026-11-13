"""FleetPulse business logic: validation, mileage, service status, cost per km.

No Streamlit code here, so everything can be tested on its own.
Dates are local calendar dates, stored as ISO text (YYYY-MM-DD).
"""
import calendar
import sqlite3
from datetime import date, datetime

import pandas as pd

VEHICLE_TYPES = ["Car", "Van", "Light commercial vehicle", "Truck", "Bus"]


# ---------- small helpers ----------
def parse_date(value):
    """Return a date from 'YYYY-MM-DD' or a date; None if invalid."""
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def add_months(d: date, months: int) -> date:
    """Add calendar months, clipping the day (31 Jan + 1 month = 28/29 Feb)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def in_range(d, start, end) -> bool:
    d = str(d)
    return (start is None or d >= start.isoformat()) and (end is None or d <= end.isoformat())


def _iso(d):
    return d.isoformat() if d else None


# ---------- Feature 1: vehicles ----------
def add_vehicle(conn, reg, vtype, odo, reg_date, last_date=None, last_odo=None,
                km_interval=None, month_interval=None, today=None):
    today = today or date.today()
    reg = (reg or "").strip().upper()
    errors = []
    if not reg:
        errors.append("Registration number is required.")
    if vtype not in VEHICLE_TYPES:
        errors.append("Choose a valid vehicle type.")
    if odo is None or odo < 0:
        errors.append("Odometer cannot be negative.")
    if reg_date is None or reg_date > today:
        errors.append("Registration date is missing or in the future.")
    if last_date and last_date > today:
        errors.append("Last service date cannot be in the future.")
    if last_odo is not None and odo is not None and last_odo > odo:
        errors.append("Last-service odometer cannot exceed the current odometer.")
    if km_interval is not None and km_interval <= 0:
        errors.append("Service interval (km) must be greater than zero.")
    if month_interval is not None and month_interval <= 0:
        errors.append("Service interval (months) must be greater than zero.")
    if errors:
        raise ValueError(" ".join(errors))
    try:
        with conn:
            conn.execute(
                "INSERT INTO vehicles (registration_number, vehicle_type, current_odometer, "
                "registration_date, last_service_date, last_service_odometer, "
                "service_interval_km, service_interval_months) VALUES (?,?,?,?,?,?,?,?)",
                (reg, vtype, odo, _iso(reg_date), _iso(last_date), last_odo, km_interval, month_interval))
    except sqlite3.IntegrityError:
        raise ValueError(f"Vehicle {reg} is already registered.")


def update_vehicle(conn, vehicle_id, vtype, odo, km_interval, month_interval):
    v = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if v is None:
        raise ValueError("Vehicle not found.")
    if odo < 0:
        raise ValueError("Odometer cannot be negative.")
    if odo < v["current_odometer"]:
        raise ValueError("Odometer cannot be reduced below the recorded reading.")
    if km_interval is not None and km_interval <= 0 or month_interval is not None and month_interval <= 0:
        raise ValueError("Service intervals must be greater than zero.")
    with conn:
        conn.execute("UPDATE vehicles SET vehicle_type=?, current_odometer=?, service_interval_km=?, "
                     "service_interval_months=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (vtype, odo, km_interval, month_interval, vehicle_id))


def set_vehicle_active(conn, vehicle_id, active: bool):
    with conn:
        conn.execute("UPDATE vehicles SET is_active=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (1 if active else 0, vehicle_id))


# ---------- Feature 2: fuel fills ----------
def add_fuel(conn, reg, fill_date, odo, litres, cost, is_full=True, notes="", today=None):
    today = today or date.today()
    v = conn.execute("SELECT id, is_active FROM vehicles WHERE registration_number=?",
                     ((reg or "").strip().upper(),)).fetchone()
    if v is None:
        raise ValueError("Vehicle not found. Register it first.")
    if not v["is_active"]:
        raise ValueError("This vehicle is inactive.")
    errors = []
    if fill_date is None or fill_date > today:
        errors.append("Fill date is missing or in the future.")
    if odo is None or odo < 0:
        errors.append("Odometer cannot be negative.")
    if litres is None or litres <= 0:
        errors.append("Litres must be greater than zero.")
    if cost is None or cost <= 0:
        errors.append("Cost must be greater than zero.")
    if errors:
        raise ValueError(" ".join(errors))
    iso = fill_date.isoformat()
    before = conn.execute("SELECT MAX(odometer_reading) m FROM fuel_records WHERE vehicle_id=? AND fill_date<=?",
                          (v["id"], iso)).fetchone()["m"]
    after = conn.execute("SELECT MIN(odometer_reading) m FROM fuel_records WHERE vehicle_id=? AND fill_date>?",
                         (v["id"], iso)).fetchone()["m"]
    if before is not None and odo < before:
        raise ValueError(f"Odometer {odo:g} is lower than an earlier fill ({before:g}).")
    if after is not None and odo > after:
        raise ValueError(f"Odometer {odo:g} is higher than a later fill ({after:g}).")
    try:
        with conn:  # one transaction: both statements succeed or neither does
            conn.execute("INSERT INTO fuel_records (vehicle_id, fill_date, odometer_reading, litres, "
                         "total_cost, is_full_fill, notes) VALUES (?,?,?,?,?,?,?)",
                         (v["id"], iso, odo, litres, cost, 1 if is_full else 0, notes or None))
            conn.execute("UPDATE vehicles SET current_odometer=MAX(current_odometer, ?), "
                         "updated_at=CURRENT_TIMESTAMP WHERE id=?", (odo, v["id"]))
    except sqlite3.IntegrityError:
        raise ValueError("A fill with this date and odometer reading already exists (duplicate).")


def fuel_records_for(conn, vehicle_id):
    rows = conn.execute("SELECT fill_date, odometer_reading, litres, total_cost, is_full_fill, notes "
                        "FROM fuel_records WHERE vehicle_id=? ORDER BY fill_date, odometer_reading",
                        (vehicle_id,)).fetchall()
    return [dict(r) for r in rows]


def fuel_df(conn, start=None, end=None, include_inactive=False):
    q = ("SELECT v.registration_number AS Vehicle, v.vehicle_type AS Type, f.fill_date, "
         "f.odometer_reading, f.litres, f.total_cost, f.is_full_fill, f.notes "
         "FROM fuel_records f JOIN vehicles v ON v.id=f.vehicle_id")
    if not include_inactive:
        q += " WHERE v.is_active=1"
    df = pd.read_sql_query(q + " ORDER BY f.fill_date", conn)
    if len(df):
        df = df[[in_range(d, start, end) for d in df["fill_date"]]]
    return df


# ---------- Feature 3: mileage (full-to-full) ----------
def mileage_intervals(records):
    """Valid full-to-full intervals from one vehicle's fuel records.

    distance = odometer at this full fill - odometer at the previous full fill
    litres   = ALL fuel added since the previous full fill, including partial fills
    A partial fill adds litres to the running total but is never a reference point.
    Intervals with distance <= 0 are skipped (not valid for mileage).
    """
    out, ref, litres, cost = [], None, 0.0, 0.0
    for r in sorted(records, key=lambda r: (str(r["fill_date"]), r["odometer_reading"])):
        if ref is not None:
            litres += r["litres"]
            cost += r["total_cost"]
        if r["is_full_fill"]:
            if ref is not None:
                dist = r["odometer_reading"] - ref
                if dist > 0 and litres > 0:
                    out.append({"fill_date": r["fill_date"], "odometer_reading": r["odometer_reading"],
                                "distance_km": dist, "litres": litres, "cost": cost,
                                "km_per_l": dist / litres})
            ref, litres, cost = r["odometer_reading"], 0.0, 0.0
    return out


def weighted_mileage(intervals):
    """Total distance / total litres (NOT the mean of per-fill km/L). None if no data."""
    litres = sum(i["litres"] for i in intervals)
    return sum(i["distance_km"] for i in intervals) / litres if litres > 0 else None


def cost_per_km(intervals):
    """Fuel cost of the eligible intervals / eligible distance. None if no distance."""
    dist = sum(i["distance_km"] for i in intervals)
    return sum(i["cost"] for i in intervals) / dist if dist > 0 else None


INTERVAL_COLS = ["Vehicle", "Type", "fill_date", "odometer_reading", "distance_km", "litres", "cost", "km_per_l"]


def all_intervals(conn, start=None, end=None, regs=None):
    rows = []
    for v in conn.execute("SELECT * FROM vehicles WHERE is_active=1 ORDER BY registration_number"):
        if regs and v["registration_number"] not in regs:
            continue
        for i in mileage_intervals(fuel_records_for(conn, v["id"])):
            if in_range(i["fill_date"], start, end):
                rows.append({"Vehicle": v["registration_number"], "Type": v["vehicle_type"], **i})
    return pd.DataFrame(rows, columns=INTERVAL_COLS)


# ---------- Feature 5: performance table and worst performer ----------
PERF_COLS = ["Vehicle", "Type", "Fills in period", "Eligible distance (km)", "Fuel for eligible distance (L)",
             "Eligible fuel cost (Rs)", "Avg mileage (km/L)", "Cost per km (Rs)", "Total fuel spend (Rs)",
             "Data status", "Rank (1 = highest cost/km)"]


def performance_table(conn, start=None, end=None, min_distance=500.0):
    rows = []
    for v in conn.execute("SELECT * FROM vehicles WHERE is_active=1 ORDER BY registration_number"):
        recs = fuel_records_for(conn, v["id"])
        in_period = [r for r in recs if in_range(r["fill_date"], start, end)]
        ivs = [i for i in mileage_intervals(recs) if in_range(i["fill_date"], start, end)]
        dist = sum(i["distance_km"] for i in ivs)
        if not recs:
            status = "Excluded: no fuel records"
        elif len(recs) == 1:
            status = "Excluded: only one fuel record"
        elif not ivs:
            status = "Excluded: no valid full-to-full interval in period"
        elif dist < min_distance:
            status = f"Excluded: eligible distance below {min_distance:g} km"
        else:
            status = "Ranked"
        rows.append({
            "Vehicle": v["registration_number"], "Type": v["vehicle_type"], "Fills in period": len(in_period),
            "Eligible distance (km)": dist, "Fuel for eligible distance (L)": sum(i["litres"] for i in ivs),
            "Eligible fuel cost (Rs)": sum(i["cost"] for i in ivs), "Avg mileage (km/L)": weighted_mileage(ivs),
            "Cost per km (Rs)": cost_per_km(ivs), "Total fuel spend (Rs)": sum(r["total_cost"] for r in in_period),
            "Data status": status, "Rank (1 = highest cost/km)": None})
    df = pd.DataFrame(rows, columns=PERF_COLS)
    ranked = df["Data status"] == "Ranked"
    if ranked.any():
        df.loc[ranked, "Rank (1 = highest cost/km)"] = df.loc[ranked, "Cost per km (Rs)"].rank(
            ascending=False, method="min")
    return df


# ---------- Feature 4: maintenance ----------
def service_status(v, today=None, soon_km=500, soon_days=15):
    """Combine distance and date criteria. Either one overdue => Overdue."""
    today = today or date.today()
    last_date = parse_date(v["last_service_date"]) if v["last_service_date"] else None
    month_int, km_int, last_odo = v["service_interval_months"], v["service_interval_km"], v["last_service_odometer"]
    next_date = add_months(last_date, month_int) if last_date and month_int else None
    next_odo = last_odo + km_int if last_odo is not None and km_int else None
    rem_km = next_odo - v["current_odometer"] if next_odo is not None else None
    rem_days = (next_date - today).days if next_date else None
    reasons = []
    if rem_km is None and rem_days is None:
        status, action = "Unknown", "Add last service date/odometer and intervals."
    elif (rem_km is not None and rem_km < 0) or (rem_days is not None and rem_days < 0):
        status, action = "Overdue", "Schedule service immediately."
        if rem_km is not None and rem_km < 0:
            reasons.append(f"{-rem_km:,.0f} km past due")
        if rem_days is not None and rem_days < 0:
            reasons.append(f"{-rem_days} days past due")
    elif (rem_km is not None and rem_km <= soon_km) or (rem_days is not None and rem_days <= soon_days):
        status, action = "Due soon", "Book a service slot this week."
        if rem_km is not None and rem_km <= soon_km:
            reasons.append(f"{rem_km:,.0f} km left")
        if rem_days is not None and rem_days <= soon_days:
            reasons.append(f"{rem_days} days left")
    else:
        status, action = "Up to date", "No action needed."
    return {"status": status, "next_date": next_date, "next_odometer": next_odo, "remaining_km": rem_km,
            "remaining_days": rem_days, "action": action, "reason": ", ".join(reasons)}


def record_maintenance(conn, reg, service_date, odo, service_type, description="", cost=0.0, today=None):
    today = today or date.today()
    v = conn.execute("SELECT * FROM vehicles WHERE registration_number=?", ((reg or "").strip().upper(),)).fetchone()
    if v is None:
        raise ValueError("Vehicle not found.")
    errors = []
    if service_date is None or service_date > today:
        errors.append("Service date is missing or in the future.")
    if odo is None or odo < 0:
        errors.append("Odometer cannot be negative.")
    if cost is None or cost < 0:
        errors.append("Cost cannot be negative.")
    if not (service_type or "").strip():
        errors.append("Service type is required.")
    if errors:
        raise ValueError(" ".join(errors))
    with conn:  # history row is always added; vehicle fields change only if this is the newest service
        conn.execute("INSERT INTO maintenance_records (vehicle_id, service_date, odometer_reading, service_type, "
                     "description, cost) VALUES (?,?,?,?,?,?)",
                     (v["id"], service_date.isoformat(), odo, service_type.strip(), description or None, cost))
        if not v["last_service_date"] or service_date.isoformat() >= v["last_service_date"]:
            conn.execute("UPDATE vehicles SET last_service_date=?, last_service_odometer=?, "
                         "current_odometer=MAX(current_odometer, ?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (service_date.isoformat(), odo, odo, v["id"]))
