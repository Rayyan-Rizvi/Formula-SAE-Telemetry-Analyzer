"""
Fictional Formula SAE-style track definition.

The track is modelled as a single distance line from 0 m to TRACK_LENGTH_M.
Every point on that line belongs to exactly one named section, which carries
the target speed and cornering characteristics the simulated driver aims for.

This is a made-up circuit. It is not based on any real venue, and the numbers
are chosen to be plausible for an FSAE autocross-style layout rather than to
model a specific car or track.
"""

# --- Track geometry ------------------------------------------------------

# Total lap distance in metres. FSAE autocross laps are typically short and
# tight compared to full-size circuits.
TRACK_LENGTH_M = 1100.0


# --- Section definitions -------------------------------------------------

# Each section describes one stretch of the lap:
#
#   name          human-readable label, used in telemetry output
#   start_m       distance at which the section begins
#   end_m         distance at which the section ends
#   target_kmh    speed the driver aims to carry through the section
#   severity      0.0 = straight, 1.0 = tightest corner on the track
#                 drives how hard the car brakes and how much steering is used
#   steering_dir  -1 left, +1 right, 0 straight
#
# Sections are contiguous: each one starts where the previous ended.

TRACK_SECTIONS = [
    {
        "name": "Main Straight",
        "start_m": 0.0,
        "end_m": 220.0,
        "target_kmh": 115.0,
        "severity": 0.0,
        "steering_dir": 0,
    },
    {
        "name": "Turn 1 Braking Zone",
        "start_m": 220.0,
        "end_m": 280.0,
        "target_kmh": 70.0,
        "severity": 0.5,
        "steering_dir": 0,
    },
    {
        "name": "Turn 1 Medium Left",
        "start_m": 280.0,
        "end_m": 390.0,
        "target_kmh": 62.0,
        "severity": 0.6,
        "steering_dir": -1,
    },
    {
        "name": "Back Straight",
        "start_m": 390.0,
        "end_m": 560.0,
        "target_kmh": 98.0,
        "severity": 0.0,
        "steering_dir": 0,
    },
    {
        "name": "Technical Esses",
        "start_m": 560.0,
        "end_m": 700.0,
        "target_kmh": 55.0,
        "severity": 0.75,
        "steering_dir": 1,
    },
    {
        "name": "Long Straight",
        "start_m": 700.0,
        "end_m": 920.0,
        "target_kmh": 120.0,
        "severity": 0.0,
        "steering_dir": 0,
    },
    {
        "name": "Hairpin",
        "start_m": 920.0,
        "end_m": 1010.0,
        "target_kmh": 38.0,
        "severity": 1.0,
        "steering_dir": -1,
    },
    {
        "name": "Final Corner",
        "start_m": 1010.0,
        "end_m": 1100.0,
        "target_kmh": 72.0,
        "severity": 0.45,
        "steering_dir": 1,
    },
]


# --- Timing sectors ------------------------------------------------------

# Sectors group sections into three contiguous timing segments, the way a
# real circuit splits a lap. Sector times are what get compared between laps.

SECTORS = [
    {"name": "Sector 1", "start_m": 0.0, "end_m": 390.0},
    {"name": "Sector 2", "start_m": 390.0, "end_m": 700.0},
    {"name": "Sector 3", "start_m": 700.0, "end_m": 1100.0},
]


# --- Lookup helpers ------------------------------------------------------


def get_section(distance_m):
    """Return the section dict containing the given lap distance.

    Distance is wrapped into [0, TRACK_LENGTH_M) so that a car which has
    driven slightly past the finish line is placed back at the start.
    """
    d = distance_m % TRACK_LENGTH_M
    for section in TRACK_SECTIONS:
        if section["start_m"] <= d < section["end_m"]:
            return section
    # Only reachable through floating-point edge cases at the very end
    # of the lap; the final section is the correct answer there.
    return TRACK_SECTIONS[-1]


def get_sector(distance_m):
    """Return the name of the timing sector containing the given distance."""
    d = distance_m % TRACK_LENGTH_M
    for sector in SECTORS:
        if sector["start_m"] <= d < sector["end_m"]:
            return sector["name"]
    return SECTORS[-1]["name"]


def get_target_speed(distance_m):
    """Return the target speed in km/h at the given lap distance."""
    return get_section(distance_m)["target_kmh"]


def upcoming_target_speed(distance_m, lookahead_m):
    """Return the lowest target speed within lookahead_m metres ahead.

    The simulated driver uses this to decide when to start braking: if the
    slowest thing coming up is much slower than the current target, it is
    time to get off the throttle. Sampling every 5 m is fine given the
    shortest section on this track is 60 m long.
    """
    lowest = get_target_speed(distance_m)
    step = 5.0
    offset = step
    while offset <= lookahead_m:
        speed_ahead = get_target_speed(distance_m + offset)
        if speed_ahead < lowest:
            lowest = speed_ahead
        offset += step
    return lowest


def validate_track():
    """Check the track definition is internally consistent.

    Raises ValueError if sections have gaps, overlap, or do not span the
    full lap. Called on import so a typo in the table fails loudly and
    immediately rather than silently producing strange telemetry.
    """
    if TRACK_SECTIONS[0]["start_m"] != 0.0:
        raise ValueError("First section must start at 0 m")

    for i, section in enumerate(TRACK_SECTIONS):
        if section["end_m"] <= section["start_m"]:
            raise ValueError(f"Section '{section['name']}' has non-positive length")
        if i > 0:
            previous_end = TRACK_SECTIONS[i - 1]["end_m"]
            if section["start_m"] != previous_end:
                raise ValueError(
                    f"Gap or overlap before section '{section['name']}': "
                    f"expected start {previous_end} m, got {section['start_m']} m"
                )

    if TRACK_SECTIONS[-1]["end_m"] != TRACK_LENGTH_M:
        raise ValueError("Last section must end at TRACK_LENGTH_M")

    if SECTORS[0]["start_m"] != 0.0 or SECTORS[-1]["end_m"] != TRACK_LENGTH_M:
        raise ValueError("Sectors must span the full lap")


validate_track()


# --- Manual inspection ---------------------------------------------------

if __name__ == "__main__":
    print(f"Track length: {TRACK_LENGTH_M:.0f} m")
    print(f"Sections: {len(TRACK_SECTIONS)}   Sectors: {len(SECTORS)}\n")

    header = f"{'Section':<22}{'Start':>8}{'End':>8}{'Length':>8}{'Target':>9}{'Sev':>6}"
    print(header)
    print("-" * len(header))

    for section in TRACK_SECTIONS:
        length = section["end_m"] - section["start_m"]
        print(
            f"{section['name']:<22}"
            f"{section['start_m']:>8.0f}"
            f"{section['end_m']:>8.0f}"
            f"{length:>8.0f}"
            f"{section['target_kmh']:>9.0f}"
            f"{section['severity']:>6.2f}"
        )

    print("\nSector boundaries:")
    for sector in SECTORS:
        print(f"  {sector['name']}: {sector['start_m']:.0f} m - {sector['end_m']:.0f} m")

    print("\nSpot checks:")
    for d in [0, 150, 250, 450, 650, 800, 960, 1050]:
        section = get_section(d)
        print(
            f"  {d:>5} m  ->  {section['name']:<22} "
            f"target {section['target_kmh']:>5.0f} km/h   {get_sector(d)}"
        )