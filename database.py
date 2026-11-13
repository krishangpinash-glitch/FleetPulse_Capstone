"""SQLite connection and schema for FleetPulse.

Tables (one-to-many): vehicles 1---* fuel_records, vehicles 1---* maintenance_records.
History is never deleted: vehicles are deactivated, and service records are appended.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "fleetpulse.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registration_number TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    current_odometer REAL NOT NULL CHECK (current_odometer >= 0),
    registration_date TEXT NOT NULL,
    last_service_date TEXT,
    last_service_odometer REAL CHECK (last_service_odometer >= 0),
    service_interval_km REAL CHECK (service_interval_km > 0),
    service_interval_months INTEGER CHECK (service_interval_months > 0),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fuel_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    fill_date TEXT NOT NULL,
    odometer_reading REAL NOT NULL CHECK (odometer_reading >= 0),
    litres REAL NOT NULL CHECK (litres > 0),
    total_cost REAL NOT NULL CHECK (total_cost > 0),
    is_full_fill INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (vehicle_id, fill_date, odometer_reading)
);
CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    service_date TEXT NOT NULL,
    odometer_reading REAL CHECK (odometer_reading >= 0),
    service_type TEXT NOT NULL,
    description TEXT,
    cost REAL NOT NULL DEFAULT 0 CHECK (cost >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
"""


def get_conn(path=None) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and rows accessible by name."""
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables on first launch (safe to call every time)."""
    conn.executescript(SCHEMA)
    conn.commit()
