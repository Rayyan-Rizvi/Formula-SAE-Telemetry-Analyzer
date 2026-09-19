"""
Telemetry cleaning, validation, and lap-level aggregation.

Takes the raw sample-level telemetry produced by generate_telemetry.py and
turns it into two things:

  1. A validated, cleaned copy of the sample data.
  2. A lap summary table with one row per lap.

The validation step exists because a telemetry pipeline should not assume
its input is perfect. Our generated data is clean by construction, so the
checks normally find nothing -- but the same code would catch dropouts,
stuck sensors, and out-of-range readings in a real logger file.
"""

import numpy as np
import pandas as pd

from src import vehicle
from src.track import SECTORS, TRACK_LENGTH_M

RAW_PATH = "data/raw/telemetry.csv"
CLEAN_PATH = "data/processed/clean_telemetry.csv"
LAP_SUMMARY_PATH = "data/processed/lap_summary.csv"
SECTOR_SUMMARY_PATH = "data/processed/sector_summary.csv"

# A sample is counted as full throttle above this, and as a braking event
# above the brake threshold. Both are judgement calls, so they live here as
# named constants rather than being buried as magic numbers in the code.
FULL_THROTTLE_THRESHOLD_PCT = 90.0
BRAKING_EVENT_THRESHOLD_PCT = 30.0

# Physically sensible ranges for each channel. Anything outside these is
# either a sensor fault or a bug in the generator.
VALID_RANGES = {
    "speed_kmh": (0.0, vehicle.MAX_SPEED_KMH + 5.0),
    "throttle_pct": (0.0, 100.0),
    "brake_pressure_pct": (0.0, 100.0),
    "rpm": (0.0, vehicle.MAX_RPM + 500.0),
    "steering_angle_deg": (-vehicle.MAX_STEERING_ANGLE_DEG - 10.0,
                           vehicle.MAX_STEERING_ANGLE_DEG + 10.0),
    "battery_voltage_v": (300.0, 420.0),
    "powertrain_temp_c": (-10.0, 200.0),
    "distance_m": (0.0, TRACK_LENGTH_M + 20.0),
}


# --- Validation ----------------------------------------------------------


def check_missing_values(df):
    """Return a dict of column name -> count of missing values."""
    missing = df.isna().sum()
    return {column: int(count) for column, count in missing.items() if count > 0}


def check_out_of_range(df):
    """Return a dict of column name -> count of readings outside valid range."""
    problems = {}
    for column, (low, high) in VALID_RANGES.items():
        if column not in df.columns:
            continue
        outside = ((df[column] < low) | (df[column] > high)).sum()
        if outside > 0:
            problems[column] = int(outside)
    return problems


def check_duplicate_samples(df):
    """Return the number of duplicated (session, lap, lap_time) rows.

    Two samples at the same instant of the same lap means something went
    wrong upstream -- a logger writing twice, or a bad merge.
    """
    key = ["session_id", "lap_number", "lap_time_s"]
    return int(df.duplicated(subset=key).sum())


def check_distance_monotonic(df):
    """Return the number of laps where distance does not increase.

    Distance along a lap should only ever go up. A decrease means samples
    are out of order or a lap boundary was detected incorrectly.
    """
    bad_laps = 0
    for _, lap in df.groupby(["session_id", "lap_number"]):
        lap = lap.sort_values("lap_time_s")
        if (lap["distance_m"].diff().dropna() < 0).any():
            bad_laps += 1
    return bad_laps


def clean_telemetry(df, verbose=True):
    """Validate and clean the telemetry, returning the cleaned DataFrame.

    Cleaning is deliberately conservative: values outside the valid range
    are clipped back to the boundary rather than dropped, because losing a
    sample breaks the even time spacing the rest of the analysis relies on.
    Missing values are filled forward, which is the standard approach for a
    brief sensor dropout.
    """
    df = df.copy()

    missing = check_missing_values(df)
    out_of_range = check_out_of_range(df)
    duplicates = check_duplicate_samples(df)
    non_monotonic = check_distance_monotonic(df)

    if verbose:
        print("Validation:")
        print(f"  Rows in:              {len(df):,}")
        print(f"  Missing values:       {sum(missing.values()) if missing else 0}")
        print(f"  Out-of-range values:  {sum(out_of_range.values()) if out_of_range else 0}")
        print(f"  Duplicate samples:    {duplicates}")
        print(f"  Non-monotonic laps:   {non_monotonic}")

        if missing:
            print(f"    missing by column:  {missing}")
        if out_of_range:
            print(f"    out of range:       {out_of_range}")

    # Fill short sensor dropouts forward, then back for any leading gap.
    numeric_columns = df.select_dtypes(include=[np.number]).columns
    df[numeric_columns] = df[numeric_columns].ffill().bfill()

    # Clip anything outside physical limits back to the boundary.
    for column, (low, high) in VALID_RANGES.items():
        if column in df.columns:
            df[column] = df[column].clip(low, high)

    # Drop exact duplicate samples, keeping the first.
    df = df.drop_duplicates(subset=["session_id", "lap_number", "lap_time_s"], keep="first")

    # Guarantee a consistent order for everything downstream.
    df = df.sort_values(["session_id", "lap_number", "lap_time_s"]).reset_index(drop=True)

    if verbose:
        print(f"  Rows out:             {len(df):,}")

    return df


# --- Lap aggregation -----------------------------------------------------


def summarise_lap(lap):
    """Return a dict of summary metrics for one lap's samples."""
    sample_count = len(lap)

    # Lap time is the last timestamp in the lap. Because samples are evenly
    # spaced in time, this is the true elapsed time, not an estimate.
    lap_time_s = float(lap["lap_time_s"].max())

    full_throttle_samples = int((lap["throttle_pct"] >= FULL_THROTTLE_THRESHOLD_PCT).sum())

    # A braking event is a contiguous run of samples above the threshold.
    # Counting transitions from "not braking" to "braking" gives the number
    # of distinct events rather than the number of braking samples.
    braking = lap["brake_pressure_pct"] >= BRAKING_EVENT_THRESHOLD_PCT
    braking_events = int((braking & ~braking.shift(1, fill_value=False)).sum())

    return {
        "session_id": lap["session_id"].iloc[0],
        "lap_number": int(lap["lap_number"].iloc[0]),
        "lap_time_s": round(lap_time_s, 2),
        "sample_count": sample_count,
        "avg_speed_kmh": round(float(lap["speed_kmh"].mean()), 2),
        "max_speed_kmh": round(float(lap["speed_kmh"].max()), 2),
        "min_speed_kmh": round(float(lap["speed_kmh"].min()), 2),
        "peak_rpm": round(float(lap["rpm"].max()), 0),
        "avg_throttle_pct": round(float(lap["throttle_pct"].mean()), 2),
        "full_throttle_pct": round(100.0 * full_throttle_samples / sample_count, 2),
        "peak_brake_pct": round(float(lap["brake_pressure_pct"].max()), 2),
        "braking_events": braking_events,
        "peak_temp_c": round(float(lap["powertrain_temp_c"].max()), 2),
        "avg_temp_c": round(float(lap["powertrain_temp_c"].mean()), 2),
        "min_voltage_v": round(float(lap["battery_voltage_v"].min()), 2),
        "max_abs_steering_deg": round(float(lap["steering_angle_deg"].abs().max()), 1),
    }


def build_lap_summary(df):
    """Return a DataFrame with one row per lap."""
    rows = []
    for _, lap in df.groupby(["session_id", "lap_number"]):
        rows.append(summarise_lap(lap))

    summary = pd.DataFrame(rows)

    # Rank laps within each session, fastest first. This is the same idea
    # as the SQL RANK() window function we use later, done in Pandas.
    summary["lap_rank_in_session"] = (
        summary.groupby("session_id")["lap_time_s"].rank(method="min").astype(int)
    )

    # Gap to the session's fastest lap, which is how lap times are normally
    # presented to a driver.
    session_best = summary.groupby("session_id")["lap_time_s"].transform("min")
    summary["gap_to_session_best_s"] = (summary["lap_time_s"] - session_best).round(2)

    return summary.sort_values(["session_id", "lap_number"]).reset_index(drop=True)


# --- Sector aggregation --------------------------------------------------


def build_sector_summary(df):
    """Return a DataFrame with one row per lap per sector.

    Sector time is derived the same way lap time is: the time elapsed
    between the first and last sample inside the sector, plus one sample
    interval to account for the gap to the next sector's first sample.
    """
    rows = []

    for (session_id, lap_number), lap in df.groupby(["session_id", "lap_number"]):
        lap = lap.sort_values("lap_time_s")

        for sector in SECTORS:
            in_sector = lap[lap["sector"] == sector["name"]]
            if in_sector.empty:
                continue

            entry_time = float(in_sector["lap_time_s"].min())
            exit_time = float(in_sector["lap_time_s"].max())

            # Samples are evenly spaced, so the interval between them is a
            # reliable estimate of the time to the next sector boundary.
            if len(in_sector) > 1:
                interval = (exit_time - entry_time) / (len(in_sector) - 1)
            else:
                interval = 0.0

            rows.append(
                {
                    "session_id": session_id,
                    "lap_number": int(lap_number),
                    "sector": sector["name"],
                    "sector_time_s": round(exit_time - entry_time + interval, 2),
                    "avg_speed_kmh": round(float(in_sector["speed_kmh"].mean()), 2),
                    "min_speed_kmh": round(float(in_sector["speed_kmh"].min()), 2),
                    "max_speed_kmh": round(float(in_sector["speed_kmh"].max()), 2),
                    "avg_throttle_pct": round(float(in_sector["throttle_pct"].mean()), 2),
                    "peak_brake_pct": round(float(in_sector["brake_pressure_pct"].max()), 2),
                }
            )

    sectors = pd.DataFrame(rows)

    # Best time in each sector across all laps of a session -- the basis of
    # a theoretical best lap.
    session_sector_best = sectors.groupby(["session_id", "sector"])["sector_time_s"].transform("min")
    sectors["gap_to_sector_best_s"] = (sectors["sector_time_s"] - session_sector_best).round(2)

    return sectors.sort_values(["session_id", "lap_number", "sector"]).reset_index(drop=True)


# --- Entry point ---------------------------------------------------------


def main():
    print(f"Reading {RAW_PATH}\n")
    raw = pd.read_csv(RAW_PATH)

    clean = clean_telemetry(raw)
    clean.to_csv(CLEAN_PATH, index=False)
    print(f"\nWrote {len(clean):,} rows to {CLEAN_PATH}")

    laps = build_lap_summary(clean)
    laps.to_csv(LAP_SUMMARY_PATH, index=False)
    print(f"Wrote {len(laps)} lap rows to {LAP_SUMMARY_PATH}")

    sectors = build_sector_summary(clean)
    sectors.to_csv(SECTOR_SUMMARY_PATH, index=False)
    print(f"Wrote {len(sectors)} sector rows to {SECTOR_SUMMARY_PATH}")

    # Show the fastest lap of each session as a sanity check.
    print("\nFastest lap per session:")
    fastest = laps.loc[laps.groupby("session_id")["lap_time_s"].idxmin()]
    for _, lap in fastest.iterrows():
        print(
            f"  {lap['session_id']}  lap {int(lap['lap_number'])}  "
            f"{lap['lap_time_s']:>6.2f} s   "
            f"top {lap['max_speed_kmh']:>6.1f} km/h   "
            f"full throttle {lap['full_throttle_pct']:>5.1f}%   "
            f"{int(lap['braking_events'])} braking events"
        )


if __name__ == "__main__":
    main()