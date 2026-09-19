"""
Synthetic telemetry generator.

Simulates a fictional Formula SAE-style car completing laps of the track
defined in src/track.py, and writes the resulting sensor readings to CSV.

The simulation advances in fixed time steps. At each step the driver model
looks at where the car is and what is coming up, chooses throttle and brake,
and the vehicle model turns those inputs into a new speed. Distance is then
advanced by speed * dt. A lap ends when distance passes the track length,
so LAP TIME IS A RESULT OF THE SIMULATION, never a generated number.

All data produced here is synthetic. No real telemetry is used.
"""

import numpy as np
import pandas as pd

from src import vehicle
from src.track import (
    SECTORS,
    TRACK_LENGTH_M,
    get_section,
    get_sector,
    upcoming_target_speed,
)

# --- Simulation settings -------------------------------------------------

DT_S = 0.1                     # time step: 10 Hz sampling
LOOKAHEAD_M = 55.0             # how far ahead the driver model looks
RANDOM_SEED = 20260918         # fixed so runs are reproducible

LAPS_PER_SESSION = 8
MAX_STEPS_PER_LAP = 3000       # safety limit; a normal lap is ~600 steps


# --- Session profiles ----------------------------------------------------

# Each session has its own conditions and driver. Without this, six sessions
# with different seeds would look nearly identical and session comparison
# would have nothing to say.
#
#   ambient_c     track temperature, drives the thermal baseline
#   aggression    >1.0 carries more corner speed and gets on power earlier
#   consistency   1.0 is metronomic; lower means more lap-to-lap variation
#   fault         a deliberately injected problem, or None
#
# The two faults are INTENTIONAL. They exist so the vehicle health analysis
# has something real to detect, and they are documented as synthetic.

SESSION_PROFILES = [
    {
        "session_id": "S01",
        "label": "Cool morning shakedown",
        "ambient_c": 16.0,
        "aggression": 0.94,
        "consistency": 0.85,
        "fault": None,
    },
    {
        "session_id": "S02",
        "label": "Baseline practice",
        "ambient_c": 21.0,
        "aggression": 1.00,
        "consistency": 0.92,
        "fault": None,
    },
    {
        "session_id": "S03",
        "label": "Hot afternoon running",
        "ambient_c": 33.0,
        "aggression": 1.02,
        "consistency": 0.90,
        "fault": None,
    },
    {
        "session_id": "S04",
        "label": "Cooling system degraded",
        "ambient_c": 29.0,
        "aggression": 1.01,
        "consistency": 0.88,
        "fault": "cooling",
    },
    {
        "session_id": "S05",
        "label": "Push session",
        "ambient_c": 24.0,
        "aggression": 1.06,
        "consistency": 0.94,
        "fault": None,
    },
    {
        "session_id": "S06",
        "label": "Weak cell under load",
        "ambient_c": 23.0,
        "aggression": 1.03,
        "consistency": 0.91,
        "fault": "battery",
    },
]


# --- Driver model --------------------------------------------------------


def choose_inputs(speed_kmh, distance_m, aggression, rng, noise_scale):
    """Decide throttle and brake for the current instant.

    Returns (throttle_pct, brake_pct), each 0-100.

    The logic is deliberately simple:
      - work out the slowest speed coming up within the lookahead window
      - if the car is well above it, brake
      - if the car is below its current target, accelerate
      - otherwise hold a maintenance throttle

    This is what makes braking start BEFORE a corner rather than in it.
    """
    section = get_section(distance_m)
    severity = section["severity"]

    # The speed the car wants to be doing right here.
    corner_limit = vehicle.cornering_speed_limit(
        section["target_kmh"], severity, aggression
    )

    # The slowest thing coming up soon. A more aggressive driver looks a
    # little less far ahead, which is why they brake later.
    lookahead = LOOKAHEAD_M * (2.0 - aggression)
    upcoming_limit = upcoming_target_speed(distance_m, lookahead) * aggression

    # How far above the upcoming limit the car currently is, as a fraction.
    overspeed = (speed_kmh - upcoming_limit) / max(upcoming_limit, 1.0)

    if overspeed > 0.02:
        # Need to slow down. Brake harder the further over the limit we are.
        brake = float(np.clip(overspeed * 180.0, 15.0, 100.0))
        throttle = 0.0
    elif speed_kmh < corner_limit * 0.98:
        # Below target and nothing slower coming up: get on the power.
        # Ease off in tight corners, since the car cannot use full throttle
        # while it is still turning hard.
        throttle = float(np.clip(100.0 * (1.0 - severity * 0.45), 25.0, 100.0))
        brake = 0.0
    else:
        # At target speed: maintenance throttle to hold it.
        throttle = float(np.clip(42.0 - severity * 12.0, 10.0, 60.0))
        brake = 0.0

    # Small random variation so the driver is not perfectly repeatable.
    throttle = float(np.clip(throttle + rng.normal(0.0, 2.5 * noise_scale), 0.0, 100.0))
    if brake > 0.0:
        brake = float(np.clip(brake + rng.normal(0.0, 2.0 * noise_scale), 0.0, 100.0))

    return throttle, brake


def update_speed(speed_kmh, throttle_pct, brake_pct, section, aggression):
    """Return the new speed after one time step, in km/h."""
    accel_ms2 = 0.0

    if brake_pct > 0.0:
        accel_ms2 -= vehicle.MAX_BRAKE_DECEL_MS2 * (brake_pct / 100.0)
    elif throttle_pct > 0.0:
        available = vehicle.available_acceleration(speed_kmh)
        accel_ms2 += available * (throttle_pct / 100.0)
        # Coasting losses apply even under power.
        accel_ms2 -= vehicle.COAST_DECEL_MS2 * 0.4
    else:
        accel_ms2 -= vehicle.COAST_DECEL_MS2

    # Convert km/h to m/s, apply the acceleration, convert back.
    speed_ms = speed_kmh / 3.6
    speed_ms = max(speed_ms + accel_ms2 * DT_S, 0.0)
    new_speed_kmh = speed_ms * 3.6

    # A corner imposes a hard grip limit the car cannot exceed, regardless
    # of what the driver does with the throttle.
    corner_limit = vehicle.cornering_speed_limit(
        section["target_kmh"], section["severity"], aggression
    )
    if section["severity"] > 0.0:
        new_speed_kmh = min(new_speed_kmh, corner_limit * 1.04)

    return float(np.clip(new_speed_kmh, 0.0, vehicle.MAX_SPEED_KMH))


# --- Derived sensor channels ---------------------------------------------


def steering_angle(distance_m, speed_kmh, rng, noise_scale):
    """Return steering wheel angle in degrees.

    Near zero on straights, larger in corners. Scales with how tight the
    corner is and rises slightly at low speed, since slow corners are the
    tight ones that need the most lock.
    """
    section = get_section(distance_m)
    severity = section["severity"]
    direction = section["steering_dir"]

    if severity == 0.0 or direction == 0:
        return float(rng.normal(0.0, 1.5 * noise_scale))

    # How far through the corner the car is, 0 at entry and 1 at exit.
    section_length = section["end_m"] - section["start_m"]
    progress = (distance_m % TRACK_LENGTH_M - section["start_m"]) / section_length
    progress = float(np.clip(progress, 0.0, 1.0))

    # Steering builds to a peak mid-corner and unwinds towards the exit.
    shape = np.sin(progress * np.pi)

    angle = vehicle.MAX_STEERING_ANGLE_DEG * severity * shape * direction
    return float(angle + rng.normal(0.0, 2.0 * noise_scale))


def update_temperature(current_temp_c, ambient_c, rpm, throttle_pct, cooling_factor):
    """Return the new powertrain temperature after one time step.

    Heat is produced in proportion to how hard the car is working, and lost
    in proportion to how far above ambient it already is. The balance of the
    two means temperature climbs during a session and levels off rather than
    rising without limit.
    """
    load = (rpm / vehicle.MAX_RPM) * (throttle_pct / 100.0)
    heat_in = vehicle.HEAT_GAIN_RATE * load * DT_S
    heat_out = vehicle.COOLING_RATE * cooling_factor * (current_temp_c - ambient_c) * DT_S
    return current_temp_c + heat_in - heat_out


def battery_voltage(throttle_pct, speed_kmh, lap_number, sag_multiplier, rng, noise_scale):
    """Return pack voltage for the current instant.

    Voltage sags under load because of the pack's internal resistance, and
    recovers when the load comes off. A slow droop across the session
    represents the pack gradually depleting.
    """
    load = (throttle_pct / 100.0) * (0.4 + 0.6 * speed_kmh / vehicle.MAX_SPEED_KMH)
    sag = vehicle.VOLTAGE_SAG_PER_LOAD * load * sag_multiplier
    droop = vehicle.VOLTAGE_DROOP_PER_LAP * (lap_number - 1)
    noise = rng.normal(0.0, 0.5 * noise_scale)
    return vehicle.NOMINAL_VOLTAGE_V - sag - droop + noise


# --- Lap and session simulation ------------------------------------------


def simulate_lap(
    session_id,
    lap_number,
    entry_speed_kmh,
    session_time_s,
    temp_c,
    profile,
    rng,
):
    """Simulate one lap and return (rows, exit_speed, session_time, temp).

    Steps forward in time until the car has covered TRACK_LENGTH_M. The
    number of steps taken IS the lap time.
    """
    rows = []

    consistency = profile["consistency"]
    noise_scale = 2.0 - consistency

    # Pace varies within a lap, not just between laps. A driver might nail
    # sector 1 and then make a mistake in sector 3 on the same lap, which
    # is what makes sector-level comparison worth doing at all.
    #
    # Each sector gets its own multiplier: a lap-wide component (the driver
    # is generally on it or not) plus an independent per-sector component.
    lap_pace = rng.normal(1.0, (1.0 - consistency) * 0.035)
    sector_pace = {}
    for sector in SECTORS:
        wobble = rng.normal(0.0, (1.0 - consistency) * 0.055)
        combined = float(np.clip(lap_pace + wobble, 0.86, 1.12))
        sector_pace[sector["name"]] = profile["aggression"] * combined

    # Faults. Both are deliberately injected so the health analysis has
    # something to find; they are synthetic, not discovered problems.
    cooling_factor = 0.45 if profile["fault"] == "cooling" else 1.0
    sag_multiplier = 2.1 if profile["fault"] == "battery" else 1.0

    distance_m = 0.0
    speed_kmh = entry_speed_kmh
    lap_time_s = 0.0

    for _ in range(MAX_STEPS_PER_LAP):
        section = get_section(distance_m)
        sector_name = get_sector(distance_m)
        lap_aggression = sector_pace[sector_name]

        throttle_pct, brake_pct = choose_inputs(
            speed_kmh, distance_m, lap_aggression, rng, noise_scale
        )

        rpm = vehicle.rpm_for_speed(speed_kmh)
        temp_c = update_temperature(
            temp_c, profile["ambient_c"], rpm, throttle_pct, cooling_factor
        )
        voltage_v = battery_voltage(
            throttle_pct, speed_kmh, lap_number, sag_multiplier, rng, noise_scale
        )
        steering_deg = steering_angle(distance_m, speed_kmh, rng, noise_scale)

        rows.append(
            {
                "session_id": session_id,
                "lap_number": lap_number,
                "session_time_s": round(session_time_s, 2),
                "lap_time_s": round(lap_time_s, 2),
                "distance_m": round(distance_m, 2),
                "track_section": section["name"],
                "sector": get_sector(distance_m),
                "speed_kmh": round(speed_kmh, 2),
                "throttle_pct": round(throttle_pct, 1),
                "brake_pressure_pct": round(brake_pct, 1),
                "rpm": round(rpm, 0),
                "steering_angle_deg": round(steering_deg, 1),
                "battery_voltage_v": round(voltage_v, 2),
                "powertrain_temp_c": round(temp_c, 2),
            }
        )

        # Advance the simulation one step.
        speed_kmh = update_speed(speed_kmh, throttle_pct, brake_pct, section, lap_aggression)
        distance_m += (speed_kmh / 3.6) * DT_S
        lap_time_s += DT_S
        session_time_s += DT_S

        if distance_m >= TRACK_LENGTH_M:
            break

    return rows, speed_kmh, session_time_s, temp_c


def simulate_session(profile, rng):
    """Simulate every lap in one session and return a list of rows."""
    rows = []

    # The car starts the out-lap from rest and carries speed across the
    # start/finish line on subsequent laps, as it would in reality.
    speed_kmh = 0.0
    session_time_s = 0.0
    temp_c = profile["ambient_c"] + vehicle.STARTUP_TEMP_OFFSET_C

    for lap_number in range(1, LAPS_PER_SESSION + 1):
        lap_rows, speed_kmh, session_time_s, temp_c = simulate_lap(
            profile["session_id"],
            lap_number,
            speed_kmh,
            session_time_s,
            temp_c,
            profile,
            rng,
        )
        rows.extend(lap_rows)

    return rows


def generate_all_sessions():
    """Simulate every session and return one combined DataFrame."""
    all_rows = []

    for index, profile in enumerate(SESSION_PROFILES):
        # Each session gets its own generator derived from the master seed,
        # so sessions are independent but the whole run is reproducible.
        rng = np.random.default_rng(RANDOM_SEED + index)
        session_rows = simulate_session(profile, rng)
        all_rows.extend(session_rows)
        print(
            f"  {profile['session_id']}  {profile['label']:<28} "
            f"{len(session_rows):>6,} samples"
        )

    return pd.DataFrame(all_rows)


# --- Entry point ---------------------------------------------------------


def main():
    print(f"Generating synthetic telemetry (seed {RANDOM_SEED})\n")

    df = generate_all_sessions()

    output_path = "data/raw/telemetry.csv"
    df.to_csv(output_path, index=False)

    print(f"\nWrote {len(df):,} rows to {output_path}")
    print(f"Sessions: {df['session_id'].nunique()}   "
          f"Laps per session: {LAPS_PER_SESSION}")

    # Quick sanity summary so problems are obvious immediately.
    lap_times = (
        df.groupby(["session_id", "lap_number"])["lap_time_s"].max().reset_index()
    )
    print(f"\nLap time range: {lap_times['lap_time_s'].min():.1f} s "
          f"to {lap_times['lap_time_s'].max():.1f} s")
    print(f"Top speed:      {df['speed_kmh'].max():.1f} km/h")
    print(f"Peak temp:      {df['powertrain_temp_c'].max():.1f} C")
    print(f"Min voltage:    {df['battery_voltage_v'].min():.1f} V")


if __name__ == "__main__":
    main()