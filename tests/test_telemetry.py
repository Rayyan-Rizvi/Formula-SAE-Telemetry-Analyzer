"""
Tests for the track model, vehicle model, generator, and cleaning.
"""

import numpy as np
import pandas as pd
import pytest

from src import vehicle
from src.clean_telemetry import clean_telemetry
from src.generate_telemetry import (
    RANDOM_SEED,
    SESSION_PROFILES,
    simulate_session,
    update_speed,
)
from src.track import TRACK_LENGTH_M, TRACK_SECTIONS, get_section


@pytest.fixture(scope="module")
def one_session():
    """Generate one session's telemetry for the bounds tests."""
    profile = SESSION_PROFILES[1]          # S02, the baseline session
    rng = np.random.default_rng(RANDOM_SEED)
    return pd.DataFrame(simulate_session(profile, rng))


def test_track_sections_are_contiguous():
    """Sections must tile the lap with no gaps or overlaps.

    A gap would make get_section() return the wrong section for distances
    inside it, which would corrupt every section-level metric silently.
    """
    assert TRACK_SECTIONS[0]["start_m"] == 0.0
    assert TRACK_SECTIONS[-1]["end_m"] == TRACK_LENGTH_M

    for previous, current in zip(TRACK_SECTIONS, TRACK_SECTIONS[1:]):
        assert current["start_m"] == previous["end_m"]

    total = sum(s["end_m"] - s["start_m"] for s in TRACK_SECTIONS)
    assert total == pytest.approx(TRACK_LENGTH_M)


def test_get_section_wraps_past_the_finish_line():
    """Distance past the finish line maps back to the start of the lap.

    The simulation loop advances distance continuously, so it overshoots
    TRACK_LENGTH_M slightly on the final step of every lap.
    """
    assert get_section(TRACK_LENGTH_M + 50.0) == get_section(50.0)
    assert get_section(TRACK_LENGTH_M)["name"] == TRACK_SECTIONS[0]["name"]


def test_generated_values_stay_within_physical_bounds(one_session):
    """Every generated channel must stay inside its physical range."""
    assert one_session["speed_kmh"].min() >= 0.0
    assert one_session["speed_kmh"].max() <= vehicle.MAX_SPEED_KMH

    assert one_session["throttle_pct"].between(0.0, 100.0).all()
    assert one_session["brake_pressure_pct"].between(0.0, 100.0).all()

    assert one_session["rpm"].min() >= vehicle.IDLE_RPM - 1.0
    assert one_session["rpm"].max() <= vehicle.MAX_RPM

    max_steering = vehicle.MAX_STEERING_ANGLE_DEG + 15.0
    assert one_session["steering_angle_deg"].abs().max() <= max_steering


def test_braking_reduces_speed_and_throttle_increases_it():
    """The core causal relationships the simulation depends on.

    If either of these inverted, every downstream metric would still
    compute cleanly while describing a car that does not make sense.
    """
    straight = TRACK_SECTIONS[0]           # Main Straight, severity 0
    entry_speed = 80.0

    braking = update_speed(entry_speed, 0.0, 100.0, straight, 1.0)
    assert braking < entry_speed

    accelerating = update_speed(entry_speed, 100.0, 0.0, straight, 1.0)
    assert accelerating > entry_speed

    coasting = update_speed(entry_speed, 0.0, 0.0, straight, 1.0)
    assert braking < coasting < accelerating


def test_lap_times_are_positive_and_plausible(one_session):
    """Lap time is counted from simulation steps, so it must be sensible.

    An 1100 m lap at roughly 70 km/h average should land near 57 s. A
    value outside 40-90 s means the simulation has gone wrong rather than
    the driver having a bad lap.
    """
    lap_times = one_session.groupby("lap_number")["lap_time_s"].max()

    assert (lap_times > 0).all()
    assert lap_times.between(40.0, 90.0).all()

    # The out-lap starts from a standstill, so it is always the slowest.
    assert lap_times.loc[1] == lap_times.max()


def test_cleaning_clips_out_of_range_values_without_dropping_rows():
    """Cleaning must repair bad values while preserving the time base.

    Dropping a sample would remove 0.1 s from the lap and silently change
    its lap time, so out-of-range values are clipped instead.
    """
    raw = pd.read_csv("data/raw/telemetry.csv", nrows=2000)

    corrupted = raw.copy()
    corrupted.loc[100, "throttle_pct"] = 250.0
    corrupted.loc[200, "speed_kmh"] = None

    cleaned = clean_telemetry(corrupted, verbose=False)

    assert len(cleaned) == len(raw)
    assert cleaned.loc[100, "throttle_pct"] == 100.0
    assert cleaned["speed_kmh"].notna().all()