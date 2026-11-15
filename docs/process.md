# PROCESS

## Overview

`PROCESS` prepares raw Citi Bike trips for modeling hourly departures and arrivals at Jersey City and Hoboken stations.

The result contains one row per station and hour. Each row combines two target counts with station, calendar, and weather features. The interval represented by a row is `[timestamp, timestamp + 1 hour)`.

The step validates and cleans trips, determines eligible station-hours, aggregates events, fills eligible hours with zero counts, joins weather, and adds calendar features. It does not modify either input DataFrame and does not depend on the results of `ANALYZE_RAW`.

The implementation is located in:

- `bikeflow/contracts/process.py` — input and output contracts and weather validation.
- `bikeflow/steps/process.py` — cleaning, coverage rules, hourly aggregation, and feature preparation.
- `bikeflow/service/weather_source.py` — a standalone Open-Meteo historical weather client.

The weather client can be used before calling PROCESS directly.

## Input

The step receives a `ProcessInput` object:

| Field | Description |
|---|---|
| `trips` | Raw trip DataFrame matching the schema in `bikeflow/contracts/raw_trips.py`. |
| `loaded_months` | Non-empty tuple of `datetime.date` values, each representing the first day of a monthly archive read in full. |
| `weather` | Prepared hourly regional weather DataFrame. Required even when coverage is incomplete. |
| `departure_buffer_hours` | Positive integer specifying how many hours to exclude at the end of each continuous source block. Default: `48`. |

Validation runs when `ProcessInput` is created. Zero, negative, boolean, and non-integer buffer values are rejected.

### Raw trips and source metadata

The raw schema contains 13 columns describing ride ID, bike type, start and end timestamps, station names and IDs, coordinates, and user type. Raw timestamps are timezone-naive local times interpreted as `America/New_York`.

Source months must be supplied explicitly; PROCESS cannot infer archive completeness from the earliest or latest trip timestamp. Month order and repeated month entries do not affect coverage construction. Missing months remain gaps.

### Prepared weather

`validate_weather()` requires exactly these four columns:

| Column | Meaning |
|---|---|
| `timestamp` | Unique, non-missing UTC timestamp aligned to the start of an hour. |
| `temperature_c` | Air temperature at 2 m at the hour start, in degrees Celsius. |
| `precipitation_mm` | Total precipitation during `[timestamp, timestamp + 1 hour)`, in millimeters. |
| `wind_speed_ms` | Wind speed at 10 m at the hour start, in meters per second. |

Feature columns must have real numeric dtypes. Known values must be finite; precipitation and wind speed must be non-negative. Negative temperatures are allowed.

Missing numeric weather values are allowed on input. They cause affected station-hours to be excluded during the join. Missing weather is never interpreted as zero precipitation, zero wind, or zero temperature.

### Weather source

`OpenMeteoArchiveSource` in `bikeflow/service/weather_source.py` retrieves historical weather from the Open-Meteo Archive API and converts it to the input contract.

The shared regional point used for JC/HB weather is:

| Constructor argument | Value |
|---|---:|
| `latitude` | `40.73` |
| `longitude` | `-74.04` |

These coordinates are supplied explicitly by the caller; they are not inferred from trip coordinates. All stations receive the same regional weather series.

The client validates coordinates and timeout, requests explicit units and UTC timestamps, and validates the response structure and values. `fetch(start, end)` requires UTC hour-aligned `pd.Timestamp` bounds and returns weather for `[start, end)`.

Open-Meteo precipitation labeled at time `t` represents the preceding hour. The adapter assigns precipitation from `t + 1 hour` to the output row at `t`, while keeping temperature and wind at their original timestamps. It requests the boundary sample needed for the final interval and aligns by exact timestamps, so missing hours are not bridged by shifting row positions.

HTTP failures and malformed responses propagate as errors. Missing numeric values remain missing for PROCESS to handle. The client supports an injected HTTP session for testing; it does not implement its own retry or caching policy.

## Processing groups

`ProcessService.process()` runs the following stages in sequence and combines the dataset and processing metadata into a `ProcessResult` object.

### 1. Trip cleaning

Implemented by `_deduplicate_trips()`, `_filter_network_trips()`, and `_prepare_trip_times()`.

Duplicate handling happens before station normalization:

- Exact copies of a consistent trip are reduced to one record.
- If one `ride_id` has conflicting original records, all records with that ID are removed.

Station IDs are stripped of surrounding whitespace, and empty IDs become missing. IDs beginning with `JC` or `HB` identify the target network.

A trip is retained if at least one endpoint belongs to that network. An external or missing endpoint does not contribute an event, but the known JC/HB endpoint still does. Records with neither endpoint in the network are removed. Missing station IDs are not reconstructed from names or coordinates.

Trip times are localized to `America/New_York` and converted to UTC. Trips with ambiguous or nonexistent local timestamps, or with a non-positive duration, are removed. Their original records are retained internally to identify affected station-hours.

### 2. Source coverage

Implemented by `_build_source_periods()` and `_build_coverage_periods()`.

Each declared source month becomes an interval from local midnight on its first day to local midnight on the next month's first day, converted to UTC. Adjacent months are merged into continuous blocks before the buffer is applied.

Monthly trip archives are partitioned by `ended_at`. A trip departing near the end of the loaded period may finish in a later, unloaded archive. Its departure would therefore be missing from the available data.

The current coverage policy subtracts `departure_buffer_hours` from the end of each continuous block. Both departures and arrivals use the remaining interval. The start is inclusive and the end is exclusive; blocks with no remaining interval produce no output hours.

**The buffer is an approximation, not a guarantee of complete departure counts.** It remains an assumption about trips that may finish outside the loaded archives.

If an otherwise valid trip lasts longer than the configured buffer, PROCESS logs a `WARNING` containing the number of such trips, the buffer, and the maximum duration. Processing continues with all valid trips and the original buffer. The buffer is not increased automatically, and duration alone does not remove these trips. Their events contribute wherever the final station-hour eligibility rules allow.

### 3. Station-hour eligibility

Implemented by `_assume_station_periods()`, `_build_excluded_hours()`, and `_build_eligible_hours()`.

Historical station opening and closure data is unavailable. Every JC/HB station found in either endpoint column of the original input is therefore assumed active throughout the study period, including before its first recorded trip and after its last recorded trip. Stations found only in subsequently rejected records are still part of this station set.

### 4. Hourly departures and arrivals

Implemented by `_aggregate_hourly_trips()` and `_complete_hourly_grid()`.

Start and end events are counted independently:

| Target | Station column | Time column |
|---|---|---|
| `departures` | `start_station_id` | `started_at` |
| `arrivals` | `end_station_id` | `ended_at` |

Each event timestamp is floored to its UTC hour. A trip crossing midnight or a month boundary contributes its departure and arrival to their respective hours and stations, provided those output hours are eligible.

All cleaned trips are aggregated before the eligible grid is applied. Consequently, an arrival inside the retained period can contribute even when the corresponding departure is outside it.

Counts are joined to the eligible station-hour grid. Missing target counts within that grid become integer zeros. The output is sorted by `station_id` and `timestamp`.

### 5. Weather enrichment

Implemented by `_join_weather()`.

Weather is joined by exact UTC `timestamp`, with many station rows matching one weather row. Any station-hour lacking one or more weather features is removed, including when the weather timestamp itself is absent.

`weather_excluded_station_hours` counts removed station-hour rows after coverage and trip-quality filtering. It does not count raw trips or distinct weather hours. For example, one missing weather hour affecting 100 eligible stations removes 100 station-hours.

### 6. Calendar features

Implemented by `_add_time_features()` and `_add_holiday_feature()`.

The UTC row key is converted to `America/New_York` only for deriving calendar features:

- `hour` — local hour, from 0 to 23.
- `weekday` — local weekday, Monday = 0 through Sunday = 6.
- `month` — local calendar month, from 1 to 12.
- `is_weekend` — whether the local date is Saturday or Sunday.
- `is_holiday` — whether the local date is a US federal holiday or its observed substitute date.

Holidays are calculated locally by the Python `holidays` package using `country_holidays("US", observed=True)`. No state-specific calendar is selected and no holiday API request is made. Adjacent years are included when building the calendar to handle observed holidays crossing a year boundary.

The full date remains available in `timestamp`. For example, `2026-09-01 04:00:00+00:00` corresponds to local midnight, so its `hour` feature is `0`. The UTC offset changes with daylight saving time; it must not be replaced by a fixed four-hour subtraction.

Year, day of month, day of year, and recent-trip lag features are not added.

## Output

The service returns a `ProcessResult` object:

| Field | Description |
|---|---|
| `dataset` | Prepared station-hour table. |
| `feature_columns` | Explicit tuple of model input column names. |
| `target_columns` | Fixed tuple: `("departures", "arrivals")`. |
| `dropped_rows` | Counts of removed original trip rows by mutually exclusive reason. |
| `weather_excluded_station_hours` | Number of otherwise eligible rows removed due to incomplete weather. |
| `loaded_months` | Source-month metadata supplied for the run. |
| `departure_buffer_hours` | Configured buffer used for the run. |
| `station_activity_assumption` | `"all_input_stations_active"`. |
| `coverage_policy` | `"departure_buffer_approximation"`. |
| `holiday_calendar` | `"US_federal_actual_and_observed"`. |

### Dataset columns

| Column | Role | Description |
|---|---|---|
| `station_id` | Key and feature | JC/HB station identifier; categorical model input. |
| `timestamp` | Key | Full date and hour start in UTC; excluded from model features. |
| `departures` | Target | Non-negative integer count of departures during the hour. |
| `arrivals` | Target | Non-negative integer count of arrivals during the hour. |
| `hour` | Feature | Local hour, 0–23. |
| `weekday` | Feature | Local weekday, Monday = 0 through Sunday = 6. |
| `month` | Feature | Local month, 1–12. |
| `is_weekend` | Feature | Boolean weekend indicator. |
| `is_holiday` | Feature | Boolean federal holiday or observed holiday indicator. |
| `temperature_c` | Feature | Temperature at the hour start, °C. |
| `precipitation_mm` | Feature | Precipitation during the hour, mm. |
| `wind_speed_ms` | Feature | Wind speed at the hour start, m/s. |

The contract validates unique `(station_id, timestamp)` keys, required columns without missing values, UTC hour alignment, and non-negative integer targets. Neither `timestamp` nor the targets may appear in `feature_columns`.

A correctly typed empty dataset is valid when no eligible station-hours remain.

### Removal counters

`dropped_rows` contains:

| Reason | Removed original trip rows |
|---|---|
| `conflicting_ride_id` | All records belonging to ride IDs with conflicting original values. |
| `duplicate_rows` | Extra exact copies of otherwise consistent records. |
| `outside_network` | Records with neither endpoint in JC/HB, except records with both station IDs missing. |
| `missing_both_stations` | Records with both station IDs missing after normalization. |
| `invalid_local_time` | Records with an ambiguous or nonexistent local timestamp. |
| `non_positive_duration` | Records with valid local timestamps but an end time at or before the start time. |

Counters follow the cleaning order and do not overlap. They count rows, not unique ride IDs. Buffer exclusions, station-hour quality exclusions, and weather exclusions are not added to these trip-removal counters. Trips exceeding the buffer are not counted as removed merely because of their duration.

## Service execution

The main entry point is `ProcessService.process(step_input)`.

The following example assumes that `trips` already contains all records from the complete August and September 2026 archives:

```python
from datetime import date

import pandas as pd

from bikeflow.contracts.process import ProcessInput
from bikeflow.service.weather_source import OpenMeteoArchiveSource
from bikeflow.steps.process import ProcessService

loaded_months = (date(2026, 8, 1), date(2026, 9, 1))
start = pd.Timestamp("2026-08-01", tz="America/New_York").tz_convert("UTC")
end = pd.Timestamp("2026-10-01", tz="America/New_York").tz_convert("UTC")

weather_source = OpenMeteoArchiveSource(
    latitude=40.73,
    longitude=-74.04,
)
weather = weather_source.fetch(start=start, end=end)

step_input = ProcessInput(
    trips=trips,
    loaded_months=loaded_months,
    weather=weather,
    departure_buffer_hours=10,
)
result = ProcessService().process(step_input)

X = result.dataset[list(result.feature_columns)]
y = result.dataset[list(result.target_columns)]
```

The explicit `10` is an example run setting; omitting it uses the contract default of `48`. Weather is fetched outside PROCESS. The service itself performs no HTTP requests or file writes and leaves `trips` and `weather` unchanged.

## Testing

Automated tests are located in:

- `tests/test_process.py` — cleaning, aggregation, coverage, calendar features, weather joins, and complete service execution.
- `tests/test_station_periods.py` — station activity assumptions and coverage intervals.
- `tests/test_contracts.py` — input and output validation.
- `tests/test_weather_source.py` — weather requests, response validation, and precipitation alignment using mocked HTTP responses.

The tests cover duplicates and conflicts, missing and external endpoints, invalid durations, daylight saving transitions, gaps between source months, zero counts, missing weather, holiday boundaries, long-trip warnings, empty results, and input DataFrame preservation.

Run the relevant automated tests with:

```bash
uv run pytest tests/test_process.py tests/test_station_periods.py tests/test_contracts.py tests/test_weather_source.py -q
```

## Limitations and considerations

- Station activity is assumed from the original input. Real openings, closures, and temporary outages are unavailable, so some generated zeros may represent periods when a station was not actually operating.
- Buffer-based coverage cannot prove completeness. Retaining unusually long trips with a warning does not resolve unknown departures whose completions lie outside the loaded archives.
- Excluded boundary or quality-affected hours are absent from the output. There is no flag preserving them as incomplete rows.
- Conservative handling of rejected records can remove otherwise valid observations from affected station-hours.
- Weather describes one regional point, rather than conditions at each station. Missing weather removes rows and can leave gaps in an otherwise continuous period.
- The archive client supplies historical weather. Future prediction requires weather available at prediction time, with matching coordinates, units, and interval semantics; this step does not implement a forecast client.

For source-data observations and archive conventions, see [Data notes](data-notes.md).
