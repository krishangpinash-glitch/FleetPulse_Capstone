import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db  # noqa: E402
import services as sv  # noqa: E402

TODAY = date(2026, 10, 14)


def rec(d, odo, litres, cost=100.0, full=True):
    return {"fill_date": d, "odometer_reading": odo, "litres": litres, "total_cost": cost, "is_full_fill": full}


@pytest.fixture
def conn():
    c = db.get_conn(":memory:")
    db.init_db(c)
    sv.add_vehicle(c, "ka01ab1234", "Truck", 5000, date(2020, 1, 1), date(2026, 8, 1), 4500, 10000, 6, today=TODAY)
    return c


# ---- mileage ----
def test_full_to_full_mileage():
    iv = sv.mileage_intervals([rec("2026-01-01", 1000, 40), rec("2026-01-10", 1400, 20)])
    assert len(iv) == 1 and iv[0]["km_per_l"] == pytest.approx(20.0)


def test_first_record_has_no_mileage():
    assert sv.mileage_intervals([rec("2026-01-01", 1000, 40)]) == []


def test_partial_fill_litres_are_added_but_not_a_reference():
    iv = sv.mileage_intervals([rec("2026-01-01", 1000, 30), rec("2026-01-05", 1200, 10, full=False),
                               rec("2026-01-10", 1500, 20)])
    assert len(iv) == 1 and iv[0]["distance_km"] == 500 and iv[0]["litres"] == 30
    assert iv[0]["km_per_l"] == pytest.approx(500 / 30)


def test_equal_odometer_is_not_valid():
    assert sv.mileage_intervals([rec("2026-01-01", 1000, 40), rec("2026-01-02", 1000, 5)]) == []


def test_weighted_average_beats_simple_mean():
    iv = [{"distance_km": 100, "litres": 10, "cost": 0}, {"distance_km": 900, "litres": 30, "cost": 0}]
    assert sv.weighted_mileage(iv) == pytest.approx(25.0)  # simple mean would be (10+30)/2 = 20
    assert sv.weighted_mileage([]) is None


def test_cost_per_km_and_no_division_by_zero():
    assert sv.cost_per_km([{"distance_km": 200, "litres": 10, "cost": 1000}]) == pytest.approx(5.0)
    assert sv.cost_per_km([]) is None


# ---- service status ----
def veh(**kw):
    base = dict(last_service_date="2026-08-01", last_service_odometer=4500, service_interval_km=10000,
                service_interval_months=6, current_odometer=5000)
    return {**base, **kw}


def test_up_to_date():
    assert sv.service_status(veh(), TODAY)["status"] == "Up to date"


def test_overdue_by_distance_only():
    s = sv.service_status(veh(current_odometer=15000), TODAY)
    assert s["status"] == "Overdue" and s["remaining_km"] == -500 and s["remaining_days"] > 0


def test_overdue_by_date_only():
    s = sv.service_status(veh(last_service_date="2026-01-01"), TODAY)
    assert s["status"] == "Overdue" and s["remaining_km"] > 0 and s["remaining_days"] < 0


def test_due_soon_and_unknown():
    assert sv.service_status(veh(current_odometer=14200), TODAY)["status"] == "Due soon"
    unknown = veh(last_service_date=None, last_service_odometer=None)
    assert sv.service_status(unknown, TODAY)["status"] == "Unknown"


def test_add_months_clips_day():
    assert sv.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)


# ---- validation and database ----
def test_duplicate_vehicle_rejected(conn):
    with pytest.raises(ValueError, match="already registered"):
        sv.add_vehicle(conn, "KA01AB1234", "Car", 0, date(2020, 1, 1), today=TODAY)


def test_negative_odometer_and_future_date_rejected(conn):
    with pytest.raises(ValueError):
        sv.add_vehicle(conn, "X1", "Car", -5, date(2020, 1, 1), today=TODAY)
    with pytest.raises(ValueError, match="future"):
        sv.add_vehicle(conn, "X2", "Car", 5, TODAY + timedelta(days=1), today=TODAY)


def test_fuel_validation(conn):
    with pytest.raises(ValueError, match="Litres"):
        sv.add_fuel(conn, "KA01AB1234", date(2026, 9, 1), 5100, 0, 500, today=TODAY)
    with pytest.raises(ValueError, match="Cost"):
        sv.add_fuel(conn, "KA01AB1234", date(2026, 9, 1), 5100, 10, -1, today=TODAY)
    with pytest.raises(ValueError, match="not found"):
        sv.add_fuel(conn, "NOPE", date(2026, 9, 1), 5100, 10, 500, today=TODAY)


def test_out_of_order_odometer_and_duplicate_rejected(conn):
    sv.add_fuel(conn, "KA01AB1234", date(2026, 9, 1), 5100, 40, 4000, today=TODAY)
    with pytest.raises(ValueError, match="lower"):
        sv.add_fuel(conn, "KA01AB1234", date(2026, 9, 5), 5050, 40, 4000, today=TODAY)
    with pytest.raises(ValueError, match="duplicate"):
        sv.add_fuel(conn, "KA01AB1234", date(2026, 9, 1), 5100, 40, 4000, today=TODAY)


def test_foreign_key_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO fuel_records (vehicle_id, fill_date, odometer_reading, litres, total_cost) "
                     "VALUES (999, '2026-01-01', 1, 1, 1)")


def test_recording_maintenance_keeps_history_and_updates_vehicle(conn):
    sv.record_maintenance(conn, "KA01AB1234", date(2026, 10, 1), 5200, "Oil change", "", 3000, today=TODAY)
    v = conn.execute("SELECT * FROM vehicles").fetchone()
    assert v["last_service_date"] == "2026-10-01" and v["last_service_odometer"] == 5200
    sv.record_maintenance(conn, "KA01AB1234", date(2026, 9, 1), 5100, "Tyres", "", 1000, today=TODAY)  # back-dated
    assert conn.execute("SELECT last_service_date FROM vehicles").fetchone()[0] == "2026-10-01"
    assert conn.execute("SELECT COUNT(*) FROM maintenance_records").fetchone()[0] == 2


# ---- ranking ----
def test_worst_performer_ranking_and_exclusions(tmp_path):
    c = db.get_conn(tmp_path / "t.db")
    db.init_db(c)
    for reg in ("A1", "B2", "C3"):
        sv.add_vehicle(c, reg, "Truck", 0, date(2020, 1, 1), today=TODAY)
    for reg, kmpl, price in (("A1", 10, 100), ("B2", 5, 100)):  # B2 costs 20/km, A1 costs 10/km
        sv.add_fuel(c, reg, date(2026, 9, 1), 1000, 40, 4000, today=TODAY)
        sv.add_fuel(c, reg, date(2026, 9, 15), 1600, 600 / kmpl, 600 / kmpl * price, today=TODAY)
    sv.add_fuel(c, "C3", date(2026, 9, 1), 1000, 40, 4000, today=TODAY)  # single record -> excluded
    df = sv.performance_table(c, min_distance=500).set_index("Vehicle")
    assert df.loc["B2", "Rank (1 = highest cost/km)"] == 1 and df.loc["A1", "Rank (1 = highest cost/km)"] == 2
    assert df.loc["B2", "Cost per km (Rs)"] == pytest.approx(20.0)
    assert df.loc["C3", "Data status"] == "Excluded: only one fuel record"
    # persistence: reopen the same file
    c.close()
    c2 = db.get_conn(tmp_path / "t.db")
    assert c2.execute("SELECT COUNT(*) FROM fuel_records").fetchone()[0] == 5
