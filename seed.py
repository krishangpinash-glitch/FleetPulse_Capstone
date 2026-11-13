"""Fictional sample data. Safe to re-run: does nothing if vehicles already exist.
Usage:  python seed.py          (add sample data to an empty database)
        python seed.py --reset  (delete the database file, then re-seed)
"""
import sys
from datetime import date, timedelta

import database as db
import services as sv


def fills(conn, reg, start_odo, n, km_step, kmpl, price, first_days_ago, gap_days, partial_at=()):
    """Create n+1 fills (a baseline plus n intervals) with a steady mileage."""
    for i in range(n + 1):
        km = km_step * i
        litres = round(km_step / kmpl, 1) if i else round(40.0, 1)
        sv.add_fuel(conn, reg, date.today() - timedelta(days=first_days_ago - i * gap_days),
                    start_odo + km, litres, round(litres * price, 2), is_full=i not in partial_at)


def seed(conn):
    t = date.today()
    # (reg, type, start odo, last service date, last service odo, km interval, month interval)
    sv.add_vehicle(conn, "KA01FP1001", "Car", 20000, t - timedelta(days=900), t - timedelta(days=60), 20000, 10000, 12)
    sv.add_vehicle(conn, "KA01FP1002", "Van", 35000, t - timedelta(days=1200), t - timedelta(days=90), 35000, 5000, 12)
    sv.add_vehicle(conn, "KA01FP1003", "Truck", 90000, t - timedelta(days=2000), t - timedelta(days=100), 90000, 20000, 6)
    sv.add_vehicle(conn, "KA01FP1004", "Light commercial vehicle", 40000, t - timedelta(days=1500), t - timedelta(days=120), 39800, 4000, 12)
    sv.add_vehicle(conn, "KA01FP1005", "Bus", 150000, t - timedelta(days=2500), t - timedelta(days=425), 149000, 30000, 6)
    sv.add_vehicle(conn, "KA01FP1006", "Van", 12000, t - timedelta(days=300), None, None, None, None)
    fills(conn, "KA01FP1001", 20000, 8, 600, 18.0, 104.5, 112, 14)             # high efficiency
    fills(conn, "KA01FP1002", 35000, 7, 700, 11.0, 104.5, 98, 14, partial_at=(3,))  # includes a partial fill; due soon by km
    fills(conn, "KA01FP1003", 90000, 6, 800, 4.5, 92.0, 84, 14)                 # high cost per km
    fills(conn, "KA01FP1004", 40000, 8, 600, 9.0, 92.0, 112, 14)                # overdue by km only
    fills(conn, "KA01FP1005", 150000, 6, 700, 5.0, 92.0, 84, 14)                # overdue by date only
    sv.add_fuel(conn, "KA01FP1006", t - timedelta(days=5), 12100, 30.0, 3135.0)  # only one fuel record
    sv.record_maintenance(conn, "KA01FP1003", t - timedelta(days=100), 90000, "Oil change", "Engine oil + filter", 6500)
    sv.record_maintenance(conn, "KA01FP1001", t - timedelta(days=60), 20000, "General service", "Routine service", 4200)


def main():
    if "--reset" in sys.argv and db.DB_PATH.exists():
        db.DB_PATH.unlink()
    conn = db.get_conn()
    db.init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]:
        print("Database already has vehicles - nothing added. Use --reset to start over.")
        return
    seed(conn)
    print("Sample data added: 6 vehicles with fuel and service history.")


if __name__ == "__main__":
    main()
