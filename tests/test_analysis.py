"""
Tests for lap aggregation, the database, lap comparison, and health checks.
"""

import pandas as pd
import pytest

from src.analysis import classify_temperature, classify_voltage, health_report
from src.database import connect, run_query
from src.generate_telemetry import LAPS_PER_SESSION, SESSION_PROFILES
from src.lap_comparison import compare_laps


def test_lap_summary_has_one_row_per_lap():
    """Aggregation must produce exactly one row per lap, no more or fewer."""
    laps = pd.read_csv("data/processed/lap_summary.csv")

    expected = len(SESSION_PROFILES) * LAPS_PER_SESSION
    assert len(laps) == expected
    assert laps["session_id"].nunique() == len(SESSION_PROFILES)

    # No session may have duplicate lap numbers.
    assert not laps.duplicated(subset=["session_id", "lap_number"]).any()


def test_fastest_lap_ranking_is_correct():
    """Rank 1 must be the minimum lap time, with a zero gap to best."""
    laps = pd.read_csv("data/processed/lap_summary.csv")

    for session_id, group in laps.groupby("session_id"):
        best = group.loc[group["lap_rank_in_session"] == 1]

        # Ties are possible and correct: RANK() gives equal lap times the
        # same position, so a session can have more than one rank-1 lap.
        assert len(best) >= 1
        assert (best["lap_time_s"] == group["lap_time_s"].min()).all()
        assert (best["gap_to_session_best_s"] == 0.0).all()
        assert (group["gap_to_session_best_s"] >= 0).all()


def test_database_tables_load_with_expected_row_counts():
    """Every table must be populated, with counts that agree with the CSVs."""
    counts = {
        table: run_query(f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0]
        for table in ["sessions", "laps", "sectors", "telemetry"]
    }

    assert counts["sessions"] == len(SESSION_PROFILES)
    assert counts["laps"] == len(SESSION_PROFILES) * LAPS_PER_SESSION
    assert counts["sectors"] == counts["laps"] * 3
    assert counts["telemetry"] == len(pd.read_csv("data/processed/clean_telemetry.csv"))


def test_foreign_key_constraint_is_enforced():
    """A lap referencing a nonexistent session must be rejected.

    SQLite ignores foreign keys unless PRAGMA foreign_keys is on, so this
    test is really checking that connect() sets that pragma.
    """
    conn = connect()
    try:
        with pytest.raises(Exception):
            conn.execute(
                """
                INSERT INTO sectors (
                    session_id, lap_number, sector, sector_time_s,
                    avg_speed_kmh, min_speed_kmh, max_speed_kmh,
                    avg_throttle_pct, peak_brake_pct
                ) VALUES ('S99', 1, 'Sector 1', 20.0, 80, 40, 110, 45, 90)
                """
            )
    finally:
        conn.rollback()
        conn.close()


def test_lap_comparison_delta_matches_the_lap_time_gap():
    """The distance-aligned comparison must reconcile with the lap times.

    These are computed by completely different routes: lap_time_s counts
    simulation timesteps, while the comparison interpolates both laps onto
    a distance grid and subtracts elapsed times. Agreement between them is
    what shows the alignment is correct.
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
    expected_gap = laps.iloc[-1]["lap_time_s"] - laps.iloc[0]["lap_time_s"]

    comparison = compare_laps("S02", fastest, "S02", slowest)
    measured_gap = comparison["time_delta_s"].iloc[-1]

    # One sample interval of tolerance, since the grid is reconstructed
    # from 10 Hz samples.
    assert measured_gap == pytest.approx(expected_gap, abs=0.15)


def test_seeded_faults_are_flagged_and_healthy_sessions_are_not():
    """The health checks must catch both seeded faults and nothing else.

    False positives matter as much as misses here: a detector that flags
    every session is no more useful than one that flags none.
    """
    report = health_report()

    flagged = set(report.loc[report["overall_status"] != "OK", "session_id"])
    seeded = set(report.loc[report["seeded_fault"].notna(), "session_id"])

    assert flagged == seeded
    assert len(seeded) == 2

    # The classifiers themselves, at the boundaries.
    assert classify_temperature(120.0) == "HIGH"
    assert classify_temperature(100.0) == "ELEVATED"
    assert classify_temperature(80.0) == "normal"
    assert classify_voltage(365.0) == "LOW"
    assert classify_voltage(372.0) == "MARGINAL"
    assert classify_voltage(385.0) == "normal"