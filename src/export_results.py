"""
CSV exports for external dashboard tools.

Power BI and Tableau work best with flat, denormalised tables: one row per
thing, all the context already joined on, readable column names. That is a
different shape from the normalised database schema, so this module does
the flattening once here rather than leaving it to be redone by hand in
the dashboard tool.


"""

import os

import pandas as pd

from src import analysis
from src.database import run_query
from src.lap_comparison import compare_laps
from src.run_sql import run_named_query

EXPORT_DIR = "data/exports"


def export_session_summary():
    """One row per session: pace, conditions, and headline metrics."""
    return run_named_query("session_summary")


def export_lap_summary():
    """One row per lap, with session context joined on.

    The session columns are repeated on every lap row. That is deliberate
    denormalisation: a dashboard can then filter laps by ambient
    temperature or driver aggression without needing a relationship.
    """
    return run_query(
        """
        SELECT
            l.session_id,
            s.label            AS session_label,
            s.ambient_temp_c,
            s.driver_aggression,
            s.driver_consistency,
            s.seeded_fault,
            l.lap_number,
            l.lap_time_s,
            l.avg_speed_kmh,
            l.max_speed_kmh,
            l.min_speed_kmh,
            l.peak_rpm,
            l.avg_throttle_pct,
            l.full_throttle_pct,
            l.peak_brake_pct,
            l.braking_events,
            l.peak_temp_c,
            l.avg_temp_c,
            l.min_voltage_v,
            l.max_abs_steering_deg,
            RANK() OVER (
                PARTITION BY l.session_id ORDER BY l.lap_time_s
            ) AS lap_rank_in_session,
            ROUND(
                l.lap_time_s - MIN(l.lap_time_s) OVER (PARTITION BY l.session_id),
                3
            ) AS gap_to_session_best_s
        FROM laps l
        JOIN sessions s ON s.session_id = l.session_id
        ORDER BY l.session_id, l.lap_number
        """
    )


def export_sector_summary():
    """One row per sector per lap, with session context."""
    return run_query(
        """
        SELECT
            sec.session_id,
            s.label AS session_label,
            sec.lap_number,
            sec.sector,
            sec.sector_time_s,
            sec.avg_speed_kmh,
            sec.min_speed_kmh,
            sec.max_speed_kmh,
            sec.avg_throttle_pct,
            sec.peak_brake_pct,
            ROUND(
                sec.sector_time_s - MIN(sec.sector_time_s) OVER (
                    PARTITION BY sec.session_id, sec.sector
                ),
                3
            ) AS gap_to_sector_best_s
        FROM sectors sec
        JOIN sessions s ON s.session_id = sec.session_id
        ORDER BY sec.session_id, sec.lap_number, sec.sector
        """
    )


def export_vehicle_health():
    """One row per session with health statuses and the numbers behind them."""
    health = analysis.health_report()
    notes = analysis.health_notes()

    if not notes.empty:
        health = health.merge(
            notes[["session_id", "observation"]], on="session_id", how="left"
        )
    else:
        health["observation"] = ""

    health["observation"] = health["observation"].fillna("")
    return health


def export_clean_telemetry(sample_every=5):
    """Sample-level telemetry, thinned for dashboard use.

    27,765 rows at 10 Hz is more than a dashboard needs and makes the file
    slow to refresh. Taking every fifth sample gives 2 Hz, which is still
    fine enough to draw a smooth speed trace but a fifth of the size.
    """
    telemetry = run_query(
        """
        SELECT
            t.session_id,
            s.label AS session_label,
            t.lap_number,
            t.lap_time_s,
            t.distance_m,
            t.track_section,
            t.sector,
            t.speed_kmh,
            t.throttle_pct,
            t.brake_pressure_pct,
            t.rpm,
            t.steering_angle_deg,
            t.battery_voltage_v,
            t.powertrain_temp_c
        FROM telemetry t
        JOIN sessions s ON s.session_id = t.session_id
        ORDER BY t.session_id, t.lap_number, t.lap_time_s
        """
    )

    return telemetry.iloc[::sample_every].reset_index(drop=True)


def export_lap_comparison():
    """Distance-aligned comparison of the baseline session's best and worst laps.

    A dashboard cannot easily do the interpolation this needs, so the
    comparison is computed here and exported as a ready made table.
    """
    laps = run_query(
        """
        SELECT lap_number, lap_time_s
        FROM laps
        WHERE session_id = 'S02' AND lap_number > 1
        ORDER BY lap_time_s
        """
    )

    fastest = int(laps.iloc[0]["lap_number"])
    slowest = int(laps.iloc[-1]["lap_number"])

    comparison = compare_laps("S02", fastest, "S02", slowest)
    comparison.insert(0, "session_id", "S02")
    comparison.insert(1, "lap_a", fastest)
    comparison.insert(2, "lap_b", slowest)

    return comparison


def export_all(verbose=True):
    """Write every export to data/exports/ and return the file list."""
    os.makedirs(EXPORT_DIR, exist_ok=True)

    exports = {
        "session_summary.csv": export_session_summary(),
        "lap_summary.csv": export_lap_summary(),
        "sector_summary.csv": export_sector_summary(),
        "vehicle_health_summary.csv": export_vehicle_health(),
        "lap_comparison.csv": export_lap_comparison(),
        "clean_telemetry.csv": export_clean_telemetry(),
    }

    written = []
    for filename, frame in exports.items():
        path = os.path.join(EXPORT_DIR, filename)
        frame.to_csv(path, index=False)
        written.append(path)
        if verbose:
            size_kb = os.path.getsize(path) / 1024
            print(f"  {filename:<32} {len(frame):>7,} rows   {size_kb:>8.0f} KB")

    return written


def main():
    print(f"Writing exports to {EXPORT_DIR}/\n")
    export_all()
    print(f"\nDone. These files are the input for the Power BI / Tableau dashboard.")


if __name__ == "__main__":
    main()