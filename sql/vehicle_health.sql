-- Formula SAE Telemetry Analyzer - vehicle health queries
--
-- Simple threshold checks over simulated telemetry. These are observations
-- about synthetic data, not safety-critical diagnostics.
--
-- Two sessions in this dataset carry deliberately injected faults (a
-- degraded cooling system and a weak battery cell). They were seeded so
-- the detection logic could be validated against a known answer.


-- name: health_flags_by_session
-- Per-session health summary with simple threshold flags.
-- CASE turns raw numbers into readable statuses, which is what you would
-- actually show someone rather than a column of temperatures.
SELECT
    s.session_id,
    s.label,
    s.ambient_temp_c,
    s.seeded_fault,
    ROUND(MAX(l.peak_temp_c), 1)   AS peak_temp_c,
    ROUND(MIN(l.min_voltage_v), 1) AS min_voltage_v,
    ROUND(MAX(l.peak_rpm), 0)      AS peak_rpm,
    CASE
        WHEN MAX(l.peak_temp_c) > 110 THEN 'HIGH'
        WHEN MAX(l.peak_temp_c) > 95  THEN 'ELEVATED'
        ELSE 'normal'
    END AS temp_status,
    CASE
        WHEN MIN(l.min_voltage_v) < 370 THEN 'LOW'
        WHEN MIN(l.min_voltage_v) < 375 THEN 'MARGINAL'
        ELSE 'normal'
    END AS voltage_status
FROM sessions s
JOIN laps l ON l.session_id = s.session_id
GROUP BY s.session_id, s.label, s.ambient_temp_c, s.seeded_fault
ORDER BY peak_temp_c DESC;


-- name: temperature_rise_per_lap
-- How much powertrain temperature climbed between consecutive laps.
--
-- LAG() gives the previous lap's peak. A healthy car heats up early and
-- then levels off as cooling catches up with heat production; a car whose
-- temperature keeps climbing lap after lap is not rejecting enough heat.
SELECT
    l.session_id,
    l.lap_number,
    ROUND(l.peak_temp_c, 1) AS peak_temp_c,
    ROUND(
        l.peak_temp_c - LAG(l.peak_temp_c) OVER (
            PARTITION BY l.session_id
            ORDER BY l.lap_number
        ),
        2
    ) AS temp_rise_since_last_lap_c,
    CASE
        WHEN LAG(l.peak_temp_c) OVER (
                 PARTITION BY l.session_id ORDER BY l.lap_number
             ) IS NULL THEN 'first lap'
        WHEN l.peak_temp_c - LAG(l.peak_temp_c) OVER (
                 PARTITION BY l.session_id ORDER BY l.lap_number
             ) > 3.0 THEN 'still climbing'
        ELSE 'stable'
    END AS thermal_trend
FROM laps l
ORDER BY l.session_id, l.lap_number;


-- name: voltage_sag_under_load
-- Voltage while working hard versus voltage while coasting.
--
-- Every pack sags under load; a pack that sags much harder than the others
-- has higher internal resistance, which is what a weak cell looks like in
-- telemetry. Comparing loaded to unloaded within the same session controls
-- for state of charge.
WITH load_split AS (
    SELECT
        session_id,
        CASE
            WHEN throttle_pct >= 80 THEN 'high_load'
            WHEN throttle_pct <= 10 THEN 'low_load'
            ELSE 'mid_load'
        END AS load_band,
        battery_voltage_v
    FROM telemetry
)
SELECT
    l.session_id,
    s.seeded_fault,
    ROUND(AVG(CASE WHEN l.load_band = 'low_load'  THEN l.battery_voltage_v END), 2) AS avg_v_coasting,
    ROUND(AVG(CASE WHEN l.load_band = 'high_load' THEN l.battery_voltage_v END), 2) AS avg_v_under_load,
    ROUND(
        AVG(CASE WHEN l.load_band = 'low_load'  THEN l.battery_voltage_v END)
      - AVG(CASE WHEN l.load_band = 'high_load' THEN l.battery_voltage_v END),
        2
    ) AS sag_v
FROM load_split l
JOIN sessions s ON s.session_id = l.session_id
GROUP BY l.session_id, s.seeded_fault
ORDER BY sag_v DESC;


-- name: high_temperature_samples
-- Individual samples above the warning threshold, grouped by where on
-- track they occurred. Tells you not just that the car got hot, but where.
SELECT
    t.session_id,
    t.track_section,
    COUNT(*) AS samples_over_95c,
    ROUND(MAX(t.powertrain_temp_c), 1) AS hottest_c,
    ROUND(AVG(t.rpm), 0) AS avg_rpm_when_hot
FROM telemetry t
WHERE t.powertrain_temp_c > 95
GROUP BY t.session_id, t.track_section
HAVING COUNT(*) > 50
ORDER BY t.session_id, samples_over_95c DESC;