# Data notes

Shared findings about the data. Both analysis and processing work on the same
raw trips from different angles: write here anything the other side should
know, with the month it was observed in and the numbers.

## Sources

| Source | What | Where |
|---|---|---|
| Trip history | Monthly Jersey City + Hoboken trips | `TRIPDATA_URL`, files matching `TRIPDATA_FILE_REGEX` (`JC-YYYYMM-citibike-tripdata.csv.zip`) |
| Inflow from New York | Trips that start in New York City and end at our stations | Same bucket, files matching `INFLOW_FILE_REGEX` (`YYYYMM-citibike-tripdata.zip`, ~1 GB); INGEST keeps only these trips, see [Inflow from New York](#inflow-from-new-york) |
| GBFS | Live station state and station info | `GBFS_URL` (`station_status`, `station_information`) |
| Weather | Hourly temperature, precipitation, wind | Open-Meteo: archive API for training, forecast API for prediction |

## Raw trip schema

Defined in code: `bikeflow/contracts/raw_trips.py` (13 columns, one row per trip,
with types, nullability and descriptions). Load a local file with
`read_raw_trips_csv(path)` to get exactly what the INGEST step will provide.

## Inflow from New York

### Why only Jersey City / Hoboken

The full New York City dataset is 45.7 million trips for 12 months (7.1 GB of
archives, ~9 GB of CSV) vs ~1 million for Jersey City / Hoboken (~200 MB in
memory). It can be processed, but only in chunks, not as one DataFrame: the
step contracts would have to change, and the model would cover ~2 000 stations
instead of 114. For our task this is not needed, so the network is JC/HB: station
ids with the prefix `JC` or `HB` (`NETWORK_STATION_PREFIXES`).

### Trips across the river

Trips are published by the region they **start** in. The JC file has every trip
that starts at our stations, including the ones that end in Manhattan, but a trip
from Manhattan to Hoboken is only in the New York City file. INGEST extracts such
trips from it, so the dataset has both directions:

| Trip | Where it comes from | 12 months |
|---|---|---|
| JC/HB → JC/HB | our file | 971 072 |
| JC/HB → New York | our file | 2 904 |
| JC/HB → no station (bike left outside a dock) | our file | 5 336 |
| New York → JC/HB | inflow file, extracted from the New York file | 3 048 |
| no station → JC/HB | our file and inflow file | 6 |
| New York → New York | not in the dataset | — |

(Counted after dropping the duplicate `ride_id`s.)

Because of the river-crossing trips the dataset has 593 station ids, and only
114 of them are ours. A New York id (479 of them) appears only as the other end
of such a trip: we see a few of its trips, not its real traffic (median 4 trips
a year, at most 587, while our busiest station has 45 393 arrivals). Together these trips are ~0.6% of
all departures and arrivals; they matter only on a few stations by the river
(see the 12-month findings below: JC072 gets 3.5% of its arrivals from New York,
HB202 2.6%).

ANALYZE_RAW describes the data as it is, New York ids included; in the station
table (`GET /runs/{id}/analyze-raw/stations`) they have `in_network: false`.
Arrivals at those stations are our trips to New York, departures from them are
the trips from New York to us.

### How PROCESS can handle it

The model predicts departures and arrivals of our stations, so the training
dataset (station × hour) needs rows only for our 114 stations, never for New
York ids. For the river-crossing trips there are two correct options:

1. **Count them on our side.** Departures are counted by `start_station_id`,
   arrivals by `end_station_id`, both only for our stations. A trip JC → New
   York is a departure of the JC station and nothing else; a trip New York → HB
   is an arrival at the HB station and nothing else. The most precise option.
2. **Drop them.** Drop every trip with a New York station at either end (keep
   trips with a missing station: a missing end is still a departure of ours).
   The dataset then has only our stations, at the cost of ~0.6% of the events
   and a slight underestimate on the riverfront stations. Drop **both**
   directions: dropping only one makes departures and arrivals of those stations
   systematically unbalanced. With this option the inflow is not needed at all
   and can be switched off with `BIKEFLOW_INFLOW_FILE_REGEX=` (empty).

Either way, also drop duplicate `ride_id`s across files (see the 12-month
findings).

### What INGEST does (`bikeflow/steps/ingest/inflow.py`)


- For every month of the window it downloads our file and the New York file of
  the same month. The window has only months published for both regions.
- It reads the New York archive in chunks of 500 000 rows and keeps the trips
  whose `end_station_id` starts with `NETWORK_STATION_PREFIXES` (`JC`, `HB`).
  The kept rows are copied unchanged into `YYYYMM-citibike-tripdata-inflow.csv`,
  and the archive is deleted.
- `scanned_trips` / `kept_trips` of every inflow file are stored in the INGEST
  step data.
- The inflow file has the same raw trip schema. Later steps read our files and
  the inflow files together, as one dataset.
- Downloads are cached in `data/cache/ingest/` by the ETag of the source file
  (for inflow: the ETag plus the prefixes). Only the extracted inflow is cached,
  not the 1 GB archive. A repeated run copies the cached files into its own folder
  in ~1 s; a new month downloads just that month; a republished file has a new
  ETag and is downloaded again. To force a full download, delete the folder.

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

### 2025-10 … 2026-09 (12 months, 972 092 trips)

**Source files**

- File names are not uniform: most months are `JC-YYYYMM-citibike-tripdata.csv.zip`,
  but 2025-10, 2026-01 and 2026-04 are `JC-YYYYMM-citibike-tripdata.zip` (no `.csv`).
  `TRIPDATA_FILE_REGEX` accepts both.
- Archives contain the CSV plus a macOS metadata entry
  (`__MACOSX/._JC-...csv`) in most months; `read_raw_trips_csv` skips it.

**Trips**

- The start station can be missing too, not only the end: 2 e-bike trips on
  2026-08-21 and 1 in 2025-08 have no start station and no start coordinates
  (most likely taken outside a dock). The raw schema allows it.

**Demand**

- Strong seasonality: 25 809 trips in 2026-02 vs 111 227 in 2026-08 (4.3×).

### 2025-11 … 2026-10 (12 months, 979 333 JC trips + 3 051 from New York)

**Inflow from New York**

- 3 051 trips came from New York into our network: 77–424 a month, 0.2–0.4%
  of all arrivals at our stations. About as many go the other way: 2 904 of our
  trips ended in New York.
- They are concentrated on the stations by the river: JC072 gets 3.5% of its
  arrivals from New York, HB202 2.6% (503 trips, the most), JC065 2.4%; all other
  stations get under 2%. 85 of our stations got at least one such trip.
- Mostly the evening commute home: 17:00–19:00 has 59% of them; 76% are
  e-bikes; median duration 27 min.
- In the New York files no trip starts at our stations (0 in 12 months), so the
  two regions never contain the same trip.
- A New York file is 1.2–5.4 million trips a month, 45.7 million for the 12
  months (7.1 GB of archives). INGEST scans them in 40 min on a home connection,
  mostly download time; memory peak 0.8 GB, the kept files take under 1 MB.
- Archive size says little about the number of trips: 2026-04 is 157 MB with
  3.9 million trips, 2026-03 is 548 MB with 2.9 million.
- The New York 2026-10 file is split by `started_at`, not by `ended_at` as ours:
  all its trips start in October, 1 467 end on November 1.
- 3 inflow trips have no start station (taken outside a dock in New York).

**Duplicates between months**

- 18 trips are in both JC-202604 and JC-202605, as identical rows: they started
  on 2026-04-30 and ended on 2026-05-01. So "a trip is in the month it ended in"
  is not always true: PROCESS must drop duplicate `ride_id`s across files.

**Ride id format**

- Up to 2026-09 `ride_id` is 16 hex chars, in both regions. From 2026-10 both
  regions use decimal numbers of 13–19 digits (`2506859557764627159`). Treat it
  as an opaque string; do not validate its format.

**Stations**

- 114 of our stations appear in 12 months vs 108 in 2026-09 alone: some stations
  are new or were inactive in some months.
- Every station a New York trip ends at is also a start station in our files, and
  every start station in our files has the `JC`/`HB` prefix: the prefix is a
  reliable test of "our station".

## Open questions

- Which stations are new or inactive in which months (114 in 12 months vs 108 in
  2026-09)?
- Do station ids or names change between months?
- How do the numbers above change across seasons (winter volume, DST months)?
