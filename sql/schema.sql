-- Formula SAE Telemetry Analyzer - database schema
--
-- Four tables in a simple star layout: sessions is the parent, laps and
-- sectors hang off it, and telemetry holds the raw samples.
--
-- All data is synthetic. See README for details.

DROP TABLE IF EXISTS telemetry;
DROP TABLE IF EXISTS sectors;
DROP TABLE IF EXISTS laps;
DROP TABLE IF EXISTS sessions;


-- One row per simulated session.
-- The profile columns record the conditions the session was generated
-- under, which is what makes cross-session comparison meaningful.
CREATE TABLE sessions (
    session_id       TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    ambient_temp_c   REAL NOT NULL,
    driver_aggression REAL NOT NULL,
    driver_consistency REAL NOT NULL,
    seeded_fault     TEXT,              -- NULL when the session is healthy
    lap_count        INTEGER NOT NULL,

    CHECK (ambient_temp_c BETWEEN -20 AND 60),
    CHECK (driver_aggression > 0),
    CHECK (driver_consistency BETWEEN 0 AND 1),
    CHECK (lap_count > 0)
);


-- One row per lap. Metrics come from the lap summary built in
-- clean_telemetry.py, so the database and the CSVs cannot disagree.
CREATE TABLE laps (
    lap_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL,
    lap_number         INTEGER NOT NULL,
    lap_time_s         REAL NOT NULL,
    sample_count       INTEGER NOT NULL,
    avg_speed_kmh      REAL NOT NULL,
    max_speed_kmh      REAL NOT NULL,
    min_speed_kmh      REAL NOT NULL,
    peak_rpm           REAL NOT NULL,
    avg_throttle_pct   REAL NOT NULL,
    full_throttle_pct  REAL NOT NULL,
    peak_brake_pct     REAL NOT NULL,
    braking_events     INTEGER NOT NULL,
    peak_temp_c        REAL NOT NULL,
    avg_temp_c         REAL NOT NULL,
    min_voltage_v      REAL NOT NULL,
    max_abs_steering_deg REAL NOT NULL,

    FOREIGN KEY (session_id) REFERENCES sessions(session_id),

    -- A session cannot have two laps with the same number.
    UNIQUE (session_id, lap_number),

    CHECK (lap_time_s > 0),
    CHECK (lap_number > 0),
    CHECK (full_throttle_pct BETWEEN 0 AND 100),
    CHECK (max_speed_kmh >= min_speed_kmh)
);


-- One row per sector per lap. Three sectors per lap.
CREATE TABLE sectors (
    sector_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    lap_number     INTEGER NOT NULL,
    sector         TEXT NOT NULL,
    sector_time_s  REAL NOT NULL,
    avg_speed_kmh  REAL NOT NULL,
    min_speed_kmh  REAL NOT NULL,
    max_speed_kmh  REAL NOT NULL,
    avg_throttle_pct REAL NOT NULL,
    peak_brake_pct REAL NOT NULL,

    FOREIGN KEY (session_id) REFERENCES sessions(session_id),

    UNIQUE (session_id, lap_number, sector),

    CHECK (sector_time_s > 0)
);


-- One row per telemetry sample. This is the large table.
CREATE TABLE telemetry (
    sample_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL,
    lap_number         INTEGER NOT NULL,
    session_time_s     REAL NOT NULL,
    lap_time_s         REAL NOT NULL,
    distance_m         REAL NOT NULL,
    track_section      TEXT NOT NULL,
    sector             TEXT NOT NULL,
    speed_kmh          REAL NOT NULL,
    throttle_pct       REAL NOT NULL,
    brake_pressure_pct REAL NOT NULL,
    rpm                REAL NOT NULL,
    steering_angle_deg REAL NOT NULL,
    battery_voltage_v  REAL NOT NULL,
    powertrain_temp_c  REAL NOT NULL,

    FOREIGN KEY (session_id) REFERENCES sessions(session_id),

    CHECK (throttle_pct BETWEEN 0 AND 100),
    CHECK (brake_pressure_pct BETWEEN 0 AND 100),
    CHECK (speed_kmh >= 0),
    CHECK (distance_m >= 0)
);


-- Indexes on the columns the analysis queries filter and join by.
-- Without these, every lap-comparison query scans all 27,820 rows.
CREATE INDEX idx_telemetry_session_lap ON telemetry(session_id, lap_number);
CREATE INDEX idx_telemetry_distance    ON telemetry(distance_m);
CREATE INDEX idx_telemetry_section     ON telemetry(track_section);
CREATE INDEX idx_laps_session          ON laps(session_id);
CREATE INDEX idx_laps_time             ON laps(lap_time_s);
CREATE INDEX idx_sectors_session_lap   ON sectors(session_id, lap_number);