-- Formula SAE Telemetry Analyzer - lap and session performance queries
--
-- Each query is preceded by a "-- name: <id>" line. src/run_sql.py splits
-- the file on those markers so individual queries can be run by name

-- name: session_summary
-- One row per session: pace, top speed, and the conditions it ran in
-- Joining laps to sessions is what lets us relate results to conditions

SELECT
    s.session_id,
    s.label,
    s.ambient_temp_c,
    s.driver_aggression,
    s.driver_consistency,
    COUNT(l.lap_id)                      AS laps_completed,
    ROUND(MIN(l.lap_time_s), 2)          AS best_lap_s,
    ROUND(AVG(l.lap_time_s), 2)          AS avg_lap_s,
    ROUND(MAX(l.lap_time_s), 2)          AS worst_lap_s,
    ROUND(MAX(l.lap_time_s) - MIN(l.lap_time_s), 2) AS lap_time_spread_s,
    ROUND(MAX(l.max_speed_kmh), 1)       AS top_speed_kmh,
    ROUND(AVG(l.avg_speed_kmh), 1)       AS avg_speed_kmh,
    ROUND(MAX(l.peak_rpm), 0)            AS peak_rpm,
    ROUND(AVG(l.full_throttle_pct), 1)   AS avg_full_throttle_pct,
    SUM(l.braking_events)                AS total_braking_events
FROM sessions s
JOIN laps l ON l.session_id = s.session_id
GROUP BY
    s.session_id, s.label, s.ambient_temp_c,
    s.driver_aggression, s.driver_consistency
ORDER BY best_lap_s;


-- name: fastest_laps_overall
-- The ten quickest laps across every session, ranked
-- RANK() is used rather than ROW_NUMBER() so that tied lap times share a
-- position, which is how timing sheets normally present results
SELECT
    RANK() OVER (ORDER BY l.lap_time_s) AS overall_rank,
    l.session_id,
    s.label,
    l.lap_number,
    l.lap_time_s,
    l.max_speed_kmh,
    l.full_throttle_pct
FROM laps l
JOIN sessions s ON s.session_id = l.session_id
ORDER BY l.lap_time_s
LIMIT 10;


-- name: lap_ranking_by_session
-- Every lap, ranked within its own session, with the gap to that
-- session's best lap. PARTITION BY restarts the ranking for each session,
-- so each one gets its own 1..8 rather than a single global ordering
SELECT
    l.session_id,
    l.lap_number,
    l.lap_time_s,
    RANK() OVER (
        PARTITION BY l.session_id
        ORDER BY l.lap_time_s
    ) AS rank_in_session,
    ROUND(
        l.lap_time_s - MIN(l.lap_time_s) OVER (PARTITION BY l.session_id),
        2
    ) AS gap_to_session_best_s
FROM laps l
ORDER BY l.session_id, l.lap_number;


-- name: lap_progression
-- How each lap compares to the one immediately before it.
-- LAG() reaches back one row within the session to get the previous lap's
-- time. This is how you see a driver building pace across a session, or
-- dropping off as tyres and temperatures go away
SELECT
    l.session_id,
    l.lap_number,
    l.lap_time_s,
    LAG(l.lap_time_s) OVER (
        PARTITION BY l.session_id
        ORDER BY l.lap_number
    ) AS previous_lap_s,
    ROUND(
        l.lap_time_s - LAG(l.lap_time_s) OVER (
            PARTITION BY l.session_id
            ORDER BY l.lap_number
        ),
        2
    ) AS delta_to_previous_s,
    CASE
        WHEN LAG(l.lap_time_s) OVER (
                 PARTITION BY l.session_id ORDER BY l.lap_number
             ) IS NULL THEN 'first lap'
        WHEN l.lap_time_s < LAG(l.lap_time_s) OVER (
                 PARTITION BY l.session_id ORDER BY l.lap_number
             ) THEN 'improved'
        ELSE 'slower'
    END AS trend
FROM laps l
ORDER BY l.session_id, l.lap_number;


-- name: sector_best_times
-- The quickest time recorded in each sector of each session, and which
-- lap set it. A CTE ranks every sector attempt, then the outer query keeps
-- only the winners
WITH ranked_sectors AS (
    SELECT
        session_id,
        sector,
        lap_number,
        sector_time_s,
        min_speed_kmh,
        max_speed_kmh,
        RANK() OVER (
            PARTITION BY session_id, sector
            ORDER BY sector_time_s
        ) AS sector_rank
    FROM sectors
)
SELECT
    session_id,
    sector,
    lap_number AS best_lap_number,
    sector_time_s AS best_sector_time_s,
    min_speed_kmh,
    max_speed_kmh
FROM ranked_sectors
WHERE sector_rank = 1
ORDER BY session_id, sector;


-- name: theoretical_best_lap
-- The lap a driver could have done by stringing together their best
-- sector times, versus the best lap they actually managed
--
-- The difference is time left on the table: if it is large, the driver
-- was quick in different places on different laps rather than putting a
-- complete lap together
WITH best_sectors AS (
    SELECT
        session_id,
        sector,
        MIN(sector_time_s) AS best_sector_time_s
    FROM sectors
    GROUP BY session_id, sector
),
theoretical AS (
    SELECT
        session_id,
        ROUND(SUM(best_sector_time_s), 2) AS theoretical_best_s
    FROM best_sectors
    GROUP BY session_id
),
actual AS (
    SELECT
        session_id,
        MIN(lap_time_s) AS actual_best_s
    FROM laps
    GROUP BY session_id
)
SELECT
    t.session_id,
    s.label,
    s.driver_consistency,
    t.theoretical_best_s,
    a.actual_best_s,
    ROUND(a.actual_best_s - t.theoretical_best_s, 2) AS time_left_on_table_s
FROM theoretical t
JOIN actual a   ON a.session_id = t.session_id
JOIN sessions s ON s.session_id = t.session_id
ORDER BY time_left_on_table_s DESC;


-- name: section_speed_profile
-- Average behaviour in each named track section, across all sessions
-- Useful for sanity-checking the simulation: the hairpin should be the
-- slowest point and the long straight the fastest
SELECT
    t.track_section,
    ROUND(AVG(t.speed_kmh), 1)          AS avg_speed_kmh,
    ROUND(MIN(t.speed_kmh), 1)          AS min_speed_kmh,
    ROUND(MAX(t.speed_kmh), 1)          AS max_speed_kmh,
    ROUND(AVG(t.throttle_pct), 1)       AS avg_throttle_pct,
    ROUND(AVG(t.brake_pressure_pct), 1) AS avg_brake_pct,
    ROUND(AVG(ABS(t.steering_angle_deg)), 1) AS avg_abs_steering_deg,
    COUNT(*)                            AS samples
FROM telemetry t
GROUP BY t.track_section
ORDER BY avg_speed_kmh DESC;


-- name: sector_comparison_by_session
-- Sector pace per session, with each sector's fastest session flagged
-- Shows whether a driver is quick everywhere or only in certain parts of
-- the lap
WITH session_sector_avg AS (
    SELECT
        session_id,
        sector,
        ROUND(AVG(sector_time_s), 2) AS avg_sector_time_s,
        ROUND(AVG(avg_speed_kmh), 1) AS avg_speed_kmh
    FROM sectors
    GROUP BY session_id, sector
)
SELECT
    session_id,
    sector,
    avg_sector_time_s,
    avg_speed_kmh,
    RANK() OVER (
        PARTITION BY sector
        ORDER BY avg_sector_time_s
    ) AS session_rank_in_sector,
    CASE
        WHEN RANK() OVER (
                 PARTITION BY sector ORDER BY avg_sector_time_s
             ) = 1 THEN 'fastest'
        ELSE ''
    END AS note
FROM session_sector_avg
ORDER BY sector, avg_sector_time_s;