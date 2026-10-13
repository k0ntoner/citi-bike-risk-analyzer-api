# Data notes

Shared findings about the data. Both analysis and processing work on the same
raw trips from different angles: write here anything the other side should
know, with the month it was observed in and the numbers.

## Sources

| Source | What | Where |
|---|---|---|
| Trip history | Monthly Jersey City + Hoboken trips | `TRIPDATA_URL`, files matching `TRIPDATA_FILE_REGEX` (`JC-YYYYMM-citibike-tripdata.csv.zip`) |
| GBFS | Live station state and station info | `GBFS_URL` (`station_status`, `station_information`) |
| Weather | Hourly temperature, precipitation, wind | Open-Meteo: archive API for training, forecast API for prediction |

## Raw trip schema

Defined in code: `bikeflow/contracts/raw_trips.py` (13 columns, one row per trip,
with types, nullability and descriptions). Load a local file with
`read_raw_trips_csv(path)` to get exactly what the INGEST step will provide.

## Findings

### 2026-09 (106 628 trips, 108 stations)

**File layout**

- The monthly file is split by `ended_at`, not `started_at`: 11 trips started on
  2026-08-31 and are in the September file. A trip belongs to the month it ended in.
- Citi Bike already removes trips shorter than 60 s: the shortest one is 60.2 s.

**Stations**

- Station ids have a prefix: `JC…` (Jersey City), `HB…` (Hoboken), or numeric like
  `5114.08` (New York). Id and name are 1:1, no renames within the month.
- Trip station ids match GBFS `station_information.short_name` for all 108 stations
  (names match too); GBFS capacity of these stations is 8–42, never 0.
- 274 trips (0.3%) ended in Manhattan, outside the JC/HB network. Not an error: it is
  a real departure from a JC/HB station but no arrival in our network.

**Trips**

- 333 trips (0.3%) have no end station, 295 of them electric bikes (left outside a
  dock). 49 of them still have `end_station_name`, so the id can be restored by name.
- 156 trips longer than 3 h, 37 longer than 24 h (max 25 h): most likely not docked.
- 3 854 round trips (same start and end station), 227 of them shorter than 2 min:
  most likely a cancelled rental or a dock problem.
- No duplicate `ride_id`, no `ended_at < started_at`, no zero or out-of-area
  coordinates, no speeds above 40 km/h.

**Time**

- Timestamps are local time without zone. September has no DST change, but the
  November file will contain the repeated 01:00–02:00 hour, so durations around it
  can be off by an hour or negative.

**Demand**

- Hourly station×hour grid: 53% of hours have zero departures, median station has
  ~1.2 departures per hour, max 57 in one hour.
- Strong commuter pattern: on weekdays JC115 gets ~40 arrivals/h at 8:00 and has
  ~40 departures/h at 17:00, so it fills up in the morning and empties in the evening.
- A calendar-only profile (weekday/weekend × hour) explains R² ≈ 0.83 of hourly
  system trips. Weather explains a large part of the rest: daytime hours with rain
  above 0.5 mm have 0.49× the typical trips; the 51 mm day (2026-09-27) had 0.2×.

## Open questions

- Do inactive or new stations appear when looking at several months?
- Do station ids or names change between months?
- How do the numbers above change across seasons (winter volume, DST months)?
