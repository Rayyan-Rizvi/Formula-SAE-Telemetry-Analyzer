"""
Fictional Formula SAE-style vehicle parameters.

These constants describe a made-up electric FSAE car. They are chosen to
produce plausible telemetry, not to model any real vehicle. Nothing here is
derived from Queen's University Racing or any other team's data.

The simulation uses a simplified point-mass model: the car accelerates when
throttle is applied, decelerates under braking and drag, and is limited by a
cornering speed that depends on how tight the corner is.
"""

# --- Performance limits --------------------------------------------------

MAX_SPEED_KMH = 125.0          # top speed the car can reach on this track
MAX_ACCEL_MS2 = 7.5            # peak acceleration at full throttle, low speed
MAX_BRAKE_DECEL_MS2 = 14.0     # peak deceleration under full braking
COAST_DECEL_MS2 = 1.2          # drag and rolling resistance when coasting

# Acceleration falls off as speed rises, since aero drag and motor torque
# characteristics both work against the car. At MAX_SPEED_KMH the available
# acceleration reaches zero.
ACCEL_FALLOFF_EXPONENT = 1.6


# --- Powertrain ----------------------------------------------------------

IDLE_RPM = 1800.0
MAX_RPM = 12000.0

# The car is modelled with a simple fixed reduction, so RPM tracks wheel
# speed directly. Real FSAE cars vary; this keeps the relationship legible.
RPM_PER_KMH = (MAX_RPM - IDLE_RPM) / MAX_SPEED_KMH


# --- Steering ------------------------------------------------------------

MAX_STEERING_ANGLE_DEG = 120.0  # steering wheel angle at full lock


# --- Thermal -------------------------------------------------------------

AMBIENT_TEMP_C = 22.0          # baseline; each session overrides this
STARTUP_TEMP_OFFSET_C = 18.0   # powertrain starts warmer than ambient

# Heat generated per second at full load, and how fast heat bleeds off to
# ambient. The ratio of these two sets the equilibrium temperature.
HEAT_GAIN_RATE = 3.2
COOLING_RATE = 0.022

MAX_SAFE_TEMP_C = 95.0         # above this the health module flags a warning


# --- Electrical ----------------------------------------------------------

NOMINAL_VOLTAGE_V = 398.0      # resting pack voltage, fully charged
VOLTAGE_SAG_PER_LOAD = 14.0    # sag at full load from internal resistance
VOLTAGE_DROOP_PER_LAP = 0.55   # gradual decline as the pack depletes

MIN_SAFE_VOLTAGE_V = 370.0     # below this the health module flags a warning


# --- Derived helpers -----------------------------------------------------


def available_acceleration(speed_kmh):
    """Return the acceleration available at the given speed, in m/s^2.

    Acceleration is highest from a standstill and tapers to zero at top
    speed. This is what stops the car accelerating forever on a straight.
    """
    if speed_kmh >= MAX_SPEED_KMH:
        return 0.0
    speed_fraction = speed_kmh / MAX_SPEED_KMH
    return MAX_ACCEL_MS2 * (1.0 - speed_fraction ** ACCEL_FALLOFF_EXPONENT)


def rpm_for_speed(speed_kmh):
    """Return engine RPM for a given road speed."""
    return IDLE_RPM + speed_kmh * RPM_PER_KMH


def cornering_speed_limit(target_kmh, severity, grip_factor):
    """Return the maximum speed the car can carry through a corner.

    target_kmh is the section's nominal target. grip_factor lets a session
    or lap run slightly above or below that target, which is how driver
    aggression and lap-to-lap variation enter the simulation.
    """
    if severity <= 0.0:
        return target_kmh
    return target_kmh * grip_factor