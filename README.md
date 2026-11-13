# FleetPulse - Fleet Fuel Efficiency and Maintenance Analytics (CP-047)

Python + Streamlit + SQLite + Pandas + Plotly. Runs locally on Windows.

## Setup (Windows PowerShell)
```powershell
python --version                      # needs 3.11 or later
cd FleetPulse_Capstone
python -m venv .venv
.venv\Scripts\Activate.ps1            # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
python seed.py                        # optional fictional sample data (safe to re-run)
streamlit run app.py                  # opens http://localhost:8501
python -m pytest -q                   # run the tests
```
The database (`data/fleetpulse.db`) is created automatically on first launch.
**Reset for a demo:** `python seed.py --reset` (deletes the DB, re-seeds). **Backup:** close the app, copy `data/fleetpulse.db`; restore by copying it back.

## Files
| File | Job |
|---|---|
| `database.py` | connection (foreign keys ON) and schema |
| `services.py` | all rules and calculations (no UI code) |
| `app.py` | Streamlit pages |
| `seed.py` | repeatable sample data |
| `tests/test_fleetpulse.py` | 18 Pytest tests |

Tables: `vehicles` 1-to-many `fuel_records` and `maintenance_records` (linked by `vehicle_id`). Vehicles are deactivated, never deleted, so history stays intact.

## The five core features
1. **Vehicles** - register, view, update, deactivate; unique registration numbers, validated inputs.
2. **Fuel fills** - validated; odometer must not go backwards; duplicates blocked; full/partial flag.
3. **Mileage** - full-to-full: distance between two full fills / all litres added since the previous full fill. Vehicle average = total km / total litres (distance-weighted, not a mean of means).
4. **Service alerts** - by km and by date; Overdue if either is passed; Due soon within the sidebar thresholds; missing data shows Unknown.
5. **Cost per km** - eligible fuel cost / eligible distance; vehicles below the minimum distance are excluded with a reason; worst = highest cost per km.

## Assumptions and limits
- Dates are local calendar dates (YYYY-MM-DD). Money is stored as floats and rounded for display (a simplification; `decimal` would be the next step).
- Mileage is only as accurate as the "full fill" flag the user enters.
- Stretch features (maintenance cost per km, sudden mileage-drop alert) are not included yet.

## Viva practice
Be ready to explain: why full-to-full and not per-fill km/L; why weighted average; how `service_status` combines km and date; why `with conn:` is a transaction; what the `UNIQUE` and `CHECK` constraints stop.
