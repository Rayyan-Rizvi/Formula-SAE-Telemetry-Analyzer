"""
Analysis layer: turns the telemetry database into readable summaries.

Reads from SQLite using the saved queries in sql/, adds the derived metrics
that are more natural to express in Python than SQL, and formats results
for printing or export.


"""

import pandas as pd

from src import vehicle
from src.database import run_query
from src.run_sql import run_named_query

# Thresholds for the health checks 
TEMP_HIGH_C = 110.0
TEMP_ELEVATED_C = 95.0
VOLTAGE_MARGINAL_V = 375.0
VOLTAGE_LOW_V = 370.0
RPM_HIGH_FRACTION = 0.97   # fraction of redline that counts as high


# Session analysis 


def session_summary():
    """Return one row per session with the headline performance metrics."""
    return run_named_query("session_summary")


def lap_detail(session_id=None):
    """Return per-lap metrics, optionally filtered to one session."""
    sql = """
        SELECT
            l.session_id,
            l.lap_number,
            l.lap_time_s,
            l.avg_speed_kmh,
            l.max_speed_kmh,
            l.peak_rpm,
            l.full_throttle_pct,
            l.peak_brake_pct,
            l.braking_events,
            l.peak_temp_c,
            l.min_voltage_v
        FROM laps l
    """
    params = None
    if session_id is not None:
        sql += " WHERE l.session_id = ?"
        params = (session_id,)
    sql += " ORDER BY l.session_id, l.lap_number"

    laps = run_query(sql, params)

    # Rank and gap are cheap to add here and keep the SQL simpler
    laps["rank_in_session"] = (
        laps.groupby("session_id")["lap_time_s"].rank(method="min").astype(int)
    )
    session_best = laps.groupby("session_id")["lap_time_s"].transform("min")
    laps["gap_to_best_s"] = (laps["lap_time_s"] - session_best).round(2)

    return laps


def consistency_metrics():
    """Return a measure of how repeatable each session's laps were.

    Standard deviation of lap times is the usual way to express this. The
    out-lap is excluded because it starts from a standstill and is always
    slower, which would swamp the real variation.
    """
    laps = run_query(
        """
        SELECT session_id, lap_number, lap_time_s
        FROM laps
        WHERE lap_number > 1
        ORDER BY session_id, lap_number
        """
    )

    grouped = laps.groupby("session_id")["lap_time_s"]
    summary = pd.DataFrame(
        {
            "laps_counted": grouped.count(),
            "mean_lap_s": grouped.mean().round(2),
            "std_dev_s": grouped.std().round(3),
            "range_s": (grouped.max() - grouped.min()).round(2),
        }
    ).reset_index()

    # Coefficient of variation expresses spread relative to pace, so a
    # slow driver is not penalised against a quick one

    summary["variation_pct"] = (
        100.0 * summary["std_dev_s"] / summary["mean_lap_s"]
    ).round(3)

    return summary.sort_values("std_dev_s").reset_index(drop=True)


# Sector analysis 


def sector_summary():
    """Return average sector performance per session."""
    return run_named_query("sector_comparison_by_session")


def sector_strengths():
    """Return each session's strongest and weakest sector relative to field.

    A session's raw sector time depends on how quick the driver is overall.

    """
    sectors = run_query(
        """
        SELECT session_id, sector, AVG(sector_time_s) AS avg_sector_time_s
        FROM sectors
        GROUP BY session_id, sector
        """
    )

    field_avg = sectors.groupby("sector")["avg_sector_time_s"].transform("mean")
    sectors["vs_field_pct"] = (
        100.0 * (sectors["avg_sector_time_s"] - field_avg) / field_avg
    ).round(2)

    rows = []
    for session_id, group in sectors.groupby("session_id"):
        best = group.loc[group["vs_field_pct"].idxmin()]
        worst = group.loc[group["vs_field_pct"].idxmax()]
        rows.append(
            {
                "session_id": session_id,
                "strongest_sector": best["sector"],
                "strongest_vs_field_pct": best["vs_field_pct"],
                "weakest_sector": worst["sector"],
                "weakest_vs_field_pct": worst["vs_field_pct"],
            }
        )

    return pd.DataFrame(rows).sort_values("session_id").reset_index(drop=True)


# Vehicle health 


def classify_temperature(peak_temp_c):
    """Return a status string for a peak powertrain temperature."""
    if peak_temp_c > TEMP_HIGH_C:
        return "HIGH"
    if peak_temp_c > TEMP_ELEVATED_C:
        return "ELEVATED"
    return "normal"


def classify_voltage(min_voltage_v):
    """Return a status string for a minimum pack voltage."""
    if min_voltage_v < VOLTAGE_LOW_V:
        return "LOW"
    if min_voltage_v < VOLTAGE_MARGINAL_V:
        return "MARGINAL"
    return "normal"


def classify_rpm(peak_rpm):
    """Return a status string for a peak RPM reading."""
    if peak_rpm > vehicle.MAX_RPM * RPM_HIGH_FRACTION:
        return "HIGH"
    return "normal"


def health_report():
    """Return a per-session health assessment with statuses and notes.

    Temperature is compared to ambient as well as in absolute terms: a car
    running 100 C on a 35 C day is less remarkable than the same reading on
    a 15 C day.
    """
    data = run_query(
        """
        SELECT
            s.session_id,
            s.label,
            s.ambient_temp_c,
            s.seeded_fault,
            MAX(l.peak_temp_c)   AS peak_temp_c,
            MIN(l.min_voltage_v) AS min_voltage_v,
            MAX(l.peak_rpm)      AS peak_rpm
        FROM sessions s
        JOIN laps l ON l.session_id = s.session_id
        GROUP BY s.session_id, s.label, s.ambient_temp_c, s.seeded_fault
        ORDER BY s.session_id
        """
    )

    data["temp_rise_over_ambient_c"] = (
        data["peak_temp_c"] - data["ambient_temp_c"]
    ).round(1)

    data["temp_status"] = data["peak_temp_c"].apply(classify_temperature)
    data["voltage_status"] = data["min_voltage_v"].apply(classify_voltage)
    data["rpm_status"] = data["peak_rpm"].apply(classify_rpm)

    # An overall status is the worst of the individual ones, so a single
    # problem is not hidden behind two normal readings.
    def overall(row):
        statuses = [row["temp_status"], row["voltage_status"], row["rpm_status"]]
        if "HIGH" in statuses or "LOW" in statuses:
            return "ATTENTION"
        if "ELEVATED" in statuses or "MARGINAL" in statuses:
            return "WATCH"
        return "OK"

    data["overall_status"] = data.apply(overall, axis=1)

    data["peak_temp_c"] = data["peak_temp_c"].round(1)
    data["min_voltage_v"] = data["min_voltage_v"].round(1)
    data["peak_rpm"] = data["peak_rpm"].round(0)

    return data


def health_notes():
    """Return a short written observation for each flagged session.

    Turning numbers into sentences is the point of a health report: a
    column of temperatures tells you less than a line saying which session
    is unusual and by how much.
    """
    report = health_report()
    field_median_rise = report["temp_rise_over_ambient_c"].median()

    notes = []
    for _, row in report.iterrows():
        session_notes = []

        if row["temp_status"] != "normal":
            excess = row["temp_rise_over_ambient_c"] - field_median_rise
            session_notes.append(
                f"peak powertrain temperature {row['peak_temp_c']:.1f} C, "
                f"{row['temp_rise_over_ambient_c']:.1f} C above ambient "
                f"({excess:+.1f} C versus the field median rise)"
            )

        if row["voltage_status"] != "normal":
            session_notes.append(
                f"minimum pack voltage {row['min_voltage_v']:.1f} V, below the "
                f"{VOLTAGE_MARGINAL_V:.0f} V watch threshold"
            )

        if row["rpm_status"] != "normal":
            session_notes.append(
                f"peak RPM {row['peak_rpm']:.0f}, within "
                f"{100 * (1 - RPM_HIGH_FRACTION):.0f}% of the {vehicle.MAX_RPM:.0f} limit"
            )

        if session_notes:
            notes.append(
                {
                    "session_id": row["session_id"],
                    "status": row["overall_status"],
                    "observation": "; ".join(session_notes),
                }
            )

    return pd.DataFrame(notes)


# Report assembly 


def print_section(title):
    """Print a section heading."""
    print(f"\n{title}")
    print("-" * len(title))


def full_report():
    """Print the complete analysis to the terminal."""
    print("=" * 72)
    print("FORMULA SAE TELEMETRY ANALYZER - SESSION REPORT")
    print("All results are from synthetic telemetry.")
    print("=" * 72)

    print_section("Session summary")
    summary = session_summary()
    print(
        summary[
            [
                "session_id", "label", "laps_completed", "best_lap_s",
                "avg_lap_s", "top_speed_kmh", "avg_full_throttle_pct",
                "total_braking_events",
            ]
        ].to_string(index=False)
    )

    print_section("Lap consistency (out-lap excluded)")
    print(consistency_metrics().to_string(index=False))

    print_section("Sector strengths relative to field average")
    print(sector_strengths().to_string(index=False))

    print_section("Vehicle health")
    health = health_report()
    print(
        health[
            [
                "session_id", "ambient_temp_c", "peak_temp_c",
                "temp_rise_over_ambient_c", "min_voltage_v", "peak_rpm",
                "temp_status", "voltage_status", "overall_status",
            ]
        ].to_string(index=False)
    )

    notes = health_notes()
    if not notes.empty:
        print_section("Health observations")
        for _, note in notes.iterrows():
            print(f"  [{note['status']}] {note['session_id']}: {note['observation']}")
    else:
        print("\n  No sessions flagged.")

    print()


def main():
    full_report()


if __name__ == "__main__":
    main()