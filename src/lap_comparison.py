"""
Lap-to-lap comparison on a common distance grid.

Two laps cannot be compared sample by sample: they have different sample
counts because they took different amounts of time, so the same index is a
different place on track. Both laps are therefore interpolated onto a
shared distance grid, after which every comparison is like-for-like.

The headline output is the cumulative time delta over distance: at each
point on track, how much time separates the two laps. Where that line
rises, the comparison lap is losing time; where it falls, it is gaining.
"""

import numpy as np
import pandas as pd

from src.database import run_query
from src.track import SECTORS, TRACK_LENGTH_M, get_section

# Spacing of the shared distance grid. 5 m gives 221 points per lap, which
# is finer than the track's shortest section (60 m) and coarse enough that
# the interpolation is not just reproducing sample noise.
GRID_SPACING_M = 5.0

# A delta must exceed this to be reported as a meaningful gain or loss.
# Below it, the difference is within the noise of a 10 Hz sample rate.
SIGNIFICANT_DELTA_S = 0.05


def load_lap(session_id, lap_number):
    """Return the samples for one lap, ordered by distance."""
    lap = run_query(
        """
        SELECT
            lap_time_s, distance_m, track_section, sector,
            speed_kmh, throttle_pct, brake_pressure_pct,
            rpm, steering_angle_deg, powertrain_temp_c
        FROM telemetry
        WHERE session_id = ? AND lap_number = ?
        ORDER BY distance_m
        """,
        (session_id, lap_number),
    )

    if lap.empty:
        raise ValueError(f"No telemetry for {session_id} lap {lap_number}")

    return lap


def build_distance_grid():
    """Return the shared distance grid both laps are interpolated onto."""
    return np.arange(0.0, TRACK_LENGTH_M + GRID_SPACING_M, GRID_SPACING_M)


def resample_to_grid(lap, grid):
    """Interpolate one lap's channels onto the shared distance grid.

    np.interp does linear interpolation: for each target distance it finds
    the two samples either side and blends them. This is appropriate here
    because the channels are continuous and sampled far more finely (every
    1-3 m at racing speeds) than the 5 m grid.
    """
    distance = lap["distance_m"].to_numpy()

    channels = [
        "lap_time_s", "speed_kmh", "throttle_pct",
        "brake_pressure_pct", "rpm", "steering_angle_deg",
    ]

    resampled = {"distance_m": grid}
    for channel in channels:
        resampled[channel] = np.interp(grid, distance, lap[channel].to_numpy())

    frame = pd.DataFrame(resampled)

    # Section and sector come from the track definition rather than from
    # the data, since they are a property of position, not of the lap.
    frame["track_section"] = [get_section(d)["name"] for d in grid]

    return frame


def compare_laps(session_a, lap_a, session_b, lap_b):
    """Compare two laps and return a per-distance comparison DataFrame.

    Lap A is the reference. A positive time_delta_s means lap B is slower
    at that point on track.
    """
    grid = build_distance_grid()

    a = resample_to_grid(load_lap(session_a, lap_a), grid)
    b = resample_to_grid(load_lap(session_b, lap_b), grid)

    comparison = pd.DataFrame(
        {
            "distance_m": grid,
            "track_section": a["track_section"],
            "speed_a_kmh": a["speed_kmh"].round(2),
            "speed_b_kmh": b["speed_kmh"].round(2),
            "speed_delta_kmh": (b["speed_kmh"] - a["speed_kmh"]).round(2),
            "throttle_a_pct": a["throttle_pct"].round(1),
            "throttle_b_pct": b["throttle_pct"].round(1),
            "throttle_delta_pct": (b["throttle_pct"] - a["throttle_pct"]).round(1),
            "brake_a_pct": a["brake_pressure_pct"].round(1),
            "brake_b_pct": b["brake_pressure_pct"].round(1),
            "brake_delta_pct": (b["brake_pressure_pct"] - a["brake_pressure_pct"]).round(1),
            # Elapsed time to reach this point on track, for each lap.
            "elapsed_a_s": a["lap_time_s"].round(3),
            "elapsed_b_s": b["lap_time_s"].round(3),
        }
    )

    # The cumulative time delta: how far apart the two laps are, in seconds,
    # by the time each reaches this point on track.
    comparison["time_delta_s"] = (
        comparison["elapsed_b_s"] - comparison["elapsed_a_s"]
    ).round(3)

    # The rate of change of the delta tells you where time is actually
    # being lost, as opposed to where the gap merely happens to be large.
    comparison["delta_rate_s_per_100m"] = (
        comparison["time_delta_s"].diff() * (100.0 / GRID_SPACING_M)
    ).round(3)

    return comparison


def sector_deltas(comparison):
    """Return the time gained or lost in each sector.

    Taking the change in cumulative delta across a sector isolates that
    sector's contribution, rather than reporting the running total.
    """
    rows = []
    for sector in SECTORS:
        in_sector = comparison[
            (comparison["distance_m"] >= sector["start_m"])
            & (comparison["distance_m"] < sector["end_m"])
        ]
        if in_sector.empty:
            continue

        entry_delta = float(in_sector["time_delta_s"].iloc[0])
        exit_delta = float(in_sector["time_delta_s"].iloc[-1])

        rows.append(
            {
                "sector": sector["name"],
                "delta_change_s": round(exit_delta - entry_delta, 3),
                "avg_speed_a_kmh": round(float(in_sector["speed_a_kmh"].mean()), 1),
                "avg_speed_b_kmh": round(float(in_sector["speed_b_kmh"].mean()), 1),
                "max_speed_deficit_kmh": round(float(in_sector["speed_delta_kmh"].min()), 1),
            }
        )

    return pd.DataFrame(rows)


def section_deltas(comparison):
    """Return the time gained or lost in each named track section."""
    rows = []
    for name, group in comparison.groupby("track_section", sort=False):
        entry_delta = float(group["time_delta_s"].iloc[0])
        exit_delta = float(group["time_delta_s"].iloc[-1])
        rows.append(
            {
                "track_section": name,
                "delta_change_s": round(exit_delta - entry_delta, 3),
                "avg_speed_delta_kmh": round(float(group["speed_delta_kmh"].mean()), 1),
                "avg_throttle_delta_pct": round(float(group["throttle_delta_pct"].mean()), 1),
                "avg_brake_delta_pct": round(float(group["brake_delta_pct"].mean()), 1),
            }
        )

    return pd.DataFrame(rows).sort_values("delta_change_s", ascending=False)


def braking_points(session_id, lap_number, threshold_pct=30.0):
    """Return the distance at which each braking zone begins.

    Braking point is one of the clearest differences between two laps: a
    driver who brakes ten metres later carries speed further down the
    straight, which is usually worth more than anything they do in the
    corner itself.
    """
    lap = load_lap(session_id, lap_number)
    braking = lap["brake_pressure_pct"] >= threshold_pct

    # A braking zone starts where braking becomes active having not been
    # active on the previous sample.
    starts = braking & ~braking.shift(1, fill_value=False)

    rows = []
    for index in lap.index[starts]:
        zone = lap.loc[index:]
        zone_end = zone[zone["brake_pressure_pct"] < threshold_pct]
        end_index = zone_end.index[0] if not zone_end.empty else lap.index[-1]
        zone_samples = lap.loc[index:end_index]

        rows.append(
            {
                "braking_start_m": round(float(lap.loc[index, "distance_m"]), 1),
                "section": lap.loc[index, "track_section"],
                "entry_speed_kmh": round(float(lap.loc[index, "speed_kmh"]), 1),
                "min_speed_kmh": round(float(zone_samples["speed_kmh"].min()), 1),
                "peak_brake_pct": round(float(zone_samples["brake_pressure_pct"].max()), 1),
            }
        )

    return pd.DataFrame(rows)


def compare_braking_points(session_a, lap_a, session_b, lap_b):
    """Match up the braking zones of two laps and report the difference."""
    a = braking_points(session_a, lap_a)
    b = braking_points(session_b, lap_b)

    rows = []
    for i in range(min(len(a), len(b))):
        rows.append(
            {
                "section": a.loc[i, "section"],
                "brake_point_a_m": a.loc[i, "braking_start_m"],
                "brake_point_b_m": b.loc[i, "braking_start_m"],
                # Positive means lap B braked later, which is usually quicker.
                "later_by_m": round(
                    b.loc[i, "braking_start_m"] - a.loc[i, "braking_start_m"], 1
                ),
                "entry_speed_delta_kmh": round(
                    b.loc[i, "entry_speed_kmh"] - a.loc[i, "entry_speed_kmh"], 1
                ),
                "min_speed_delta_kmh": round(
                    b.loc[i, "min_speed_kmh"] - a.loc[i, "min_speed_kmh"], 1
                ),
            }
        )

    return pd.DataFrame(rows)


def summarise_comparison(session_a, lap_a, session_b, lap_b):
    """Return a printable summary of where the two laps differed."""
    comparison = compare_laps(session_a, lap_a, session_b, lap_b)

    total_delta = float(comparison["time_delta_s"].iloc[-1])
    sections = section_deltas(comparison)

    worst = sections.iloc[0]
    best = sections.iloc[-1]

    return {
        "comparison": comparison,
        "sectors": sector_deltas(comparison),
        "sections": sections,
        "braking": compare_braking_points(session_a, lap_a, session_b, lap_b),
        "total_delta_s": round(total_delta, 3),
        "biggest_loss_section": worst["track_section"],
        "biggest_loss_s": worst["delta_change_s"],
        "biggest_gain_section": best["track_section"],
        "biggest_gain_s": best["delta_change_s"],
    }


# --- Printable report ----------------------------------------------------


def print_comparison(session_a, lap_a, session_b, lap_b):
    """Print a full comparison of two laps."""
    result = summarise_comparison(session_a, lap_a, session_b, lap_b)

    label_a = f"{session_a} lap {lap_a}"
    label_b = f"{session_b} lap {lap_b}"

    print("=" * 72)
    print(f"LAP COMPARISON: {label_b} versus {label_a}")
    print("Synthetic telemetry; lap A is the reference.")
    print("=" * 72)

    comparison = result["comparison"]
    print(f"\nFinal time delta: {result['total_delta_s']:+.3f} s")
    if result["total_delta_s"] > 0:
        print(f"  {label_b} was slower over the lap.")
    else:
        print(f"  {label_b} was faster over the lap.")

    print("\nTime change by sector")
    print("-" * 21)
    print(result["sectors"].to_string(index=False))

    print("\nTime change by section (worst first)")
    print("-" * 35)
    print(result["sections"].to_string(index=False))

    print("\nBraking points")
    print("-" * 14)
    braking = result["braking"]
    if braking.empty:
        print("  No matching braking zones found.")
    else:
        print(braking.to_string(index=False))

    print(
        f"\nBiggest loss: {result['biggest_loss_section']} "
        f"({result['biggest_loss_s']:+.3f} s)"
    )
    print(
        f"Biggest gain: {result['biggest_gain_section']} "
        f"({result['biggest_gain_s']:+.3f} s)"
    )

    # Where the delta was changing fastest, which is where the driver
    # actually lost the time rather than where the gap was widest.
    significant = comparison[
        comparison["delta_rate_s_per_100m"].abs() > SIGNIFICANT_DELTA_S
    ]
    if not significant.empty:
        steepest = significant.loc[
            significant["delta_rate_s_per_100m"].abs().idxmax()
        ]
        print(
            f"\nSteepest change at {steepest['distance_m']:.0f} m "
            f"({steepest['track_section']}): "
            f"{steepest['delta_rate_s_per_100m']:+.3f} s per 100 m"
        )

    print()


def main():
    # Compare the fastest and slowest laps of the baseline session, which
    # is the comparison a driver would actually want to see.
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

    print_comparison("S02", fastest, "S02", slowest)


if __name__ == "__main__":
    main()