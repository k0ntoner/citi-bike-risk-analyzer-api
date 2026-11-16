# ANALYZE_RAW

## Overview

`ANALYZE_RAW` is an informational pipeline step responsible for analyzing the raw Citi Bike trip dataset.

Its purpose is to calculate general statistics, evaluate data quality, identify unusual trips, analyze station activity, and describe trip distributions.

The step does not modify the input data. It receives the same raw trips DataFrame that is also used by the `PROCESS` step and returns structured analysis results.

The implementation is located in:

- `bikeflow/steps/analyze_raw.py` — analysis logic.
- `bikeflow/contracts/analyze_raw.py` — input and output data contracts.
- `tests/test_analyze_raw.py` — unit and service tests.

## Input

The step receives an `AnalyzeRawInput` object containing a pandas DataFrame with raw trip records.

The DataFrame must comply with the schema defined in `bikeflow/contracts/raw_trips.py`. The schema contains 13 columns describing:

- Ride ID.
- Bike type.
- Trip start and end timestamps.
- Start and end station names and IDs.
- Start and end coordinates.
- User type (`member` or `casual`).

Input validation is performed by `raw_trips.validate()` when an `AnalyzeRawInput` object is created.

The input DataFrame must not be modified because it is shared with the `PROCESS` step.

## Analysis groups

The `AnalyzeRawService.analyze_raw()` method performs five independent analysis groups and combines their results into an `AnalyzeRawResult` object.

### 1. General statistics

Implemented by `_analyze_general_statistics()`.

This group calculates basic information about the dataset:

- `total_trips` — total number of trip records.
- `period_start` — earliest trip start timestamp (`started_at`).
- `period_end` — latest trip end timestamp (`ended_at`).
- `unique_stations` — number of unique station IDs found in both start and end station columns.

Station IDs are combined from `start_station_id` and `end_station_id`. Missing IDs are excluded from the unique station count.

For an empty dataset, `period_start` and `period_end` are `None`.

### 2. Data quality

Implemented by `_analyze_data_quality()`.

This group identifies missing values, duplicate ride IDs, and invalid records.

It calculates:

- `missing_values` — number of missing values in every DataFrame column.
- `duplicate_ride_ids` — number of repeated ride IDs, excluding the first occurrence of each ID.
- `invalid_bike_type` — records where the bike type is not `classic_bike` or `electric_bike`.
- `invalid_user_type` — records where the user type is not `member` or `casual`.
- `invalid_coordinates` — number of records containing at least one coordinate outside the valid geographic ranges.
- `end_before_start` — number of trips where `ended_at` is earlier than `started_at`.

Coordinate limits:

| Coordinate | Minimum | Maximum |
|---|---:|---:|
| Latitude | -90 | 90 |
| Longitude | -180 | 180 |

Missing values in nullable columns are counted as missing data, but are not automatically classified as invalid records.

The `invalid_records` dictionary contains the four invalid-record counters: `invalid_bike_type`, `invalid_user_type`, `invalid_coordinates`, and `end_before_start`.

### 3. Trip anomalies

Implemented by `_analyze_trip_anomalies()`.

This group analyzes trip durations and identifies unusual trips.

It calculates:

- `long_rides` — trips lasting strictly longer than 3 hours.
- `short_round_trips` — trips that start and end at the same known station and last from 0 up to, but not including, 2 minutes.
- `zero_duration` — trips whose start and end timestamps are equal.
- `negative_duration` — trips where the end timestamp is earlier than the start timestamp.

A round trip is identified by comparing `start_station_id` and `end_station_id`. Both station IDs must be present.

The duration is calculated in seconds using the difference between `ended_at` and `started_at`.

**Important:** anomaly categories may overlap. For example, a zero-duration trip that starts and ends at the same station is counted both as a `short_round_trip` and as a `zero_duration` trip.

### 4. Station analysis

Implemented by `_analyze_station_analysis()`.

This group describes the activity of each station.

For every station ID found in the start or end station columns, it calculates:

- `station_id` — station identifier.
- `station_name` — first available non-missing station name found for that ID.
- `departures` — number of trips starting at the station.
- `arrivals` — number of trips ending at the station.

The result also contains:

- `total_stations` — total number of unique station IDs found in the trip records.
- `stations_without_departures` — stations with no recorded departures.
- `stations_without_arrivals` — stations with no recorded arrivals.

Stations are sorted by their station IDs.

This analysis uses station IDs present in the trip dataset. It does not independently retrieve inactive stations or stations from GBFS.

### 5. Distributions

Implemented by `_analyze_distributions()`.

This group calculates the distribution of trips across several categories and time periods.

It returns:

| Field | Description |
|---|---|
| `by_bike_type` | Number of trips for each bike type. |
| `by_user_type` | Number of trips for each user type. |
| `by_hour` | Number of trips grouped by the start hour (0–23). |
| `by_weekday` | Number of trips grouped by the weekday. |
| `by_month` | Number of trips grouped by calendar month (1–12). |

Weekday numbering follows pandas conventions:

- Monday = 0
- Tuesday = 1
- Wednesday = 2
- Thursday = 3
- Friday = 4
- Saturday = 5
- Sunday = 6

The month distribution is grouped by calendar month number, without separating different years. For example, September records from different years are combined into the same month category.

The distribution calculations use `value_counts()`, which excludes missing category values. Missing values are counted separately in the data quality analysis.

## Output

The service returns an `AnalyzeRawResult` object containing five result groups:

```text
AnalyzeRawResult
├── general_statistics: GeneralStatistics
├── data_quality: DataQuality
├── trip_anomalies: TripAnomalies
├── station_analysis: StationAnalysis
└── distributions: Distributions
```

All result structures are defined as frozen dataclasses in `bikeflow/contracts/analyze_raw.py`.

The output is structured data rather than HTML. This allows other application components to use the analysis results and render them in the user interface.

## Service execution

The main entry point is:

```python
from bikeflow.contracts.analyze_raw import AnalyzeRawInput
from bikeflow.steps.analyze_raw import AnalyzeRawService

step_input = AnalyzeRawInput(trips=trips)

service = AnalyzeRawService()
result = service.analyze_raw(step_input)
```

The service calls all five analysis functions and combines their results into one `AnalyzeRawResult`.

The input DataFrame remains unchanged throughout the analysis.

## Testing

Tests are located in `tests/test_analyze_raw.py`.

They cover:

- General statistics, including an empty dataset.
- Missing values, duplicate IDs, and invalid records.
- Long rides, short round trips, zero-duration and negative-duration trips.
- Station departures, arrivals, and stations without activity in one direction.
- Distributions by bike type, user type, hour, weekday, and month.
- Complete service execution and input DataFrame immutability.

Run the project tests with:

```bash
uv run pytest
```

## Limitations and considerations

- The step analyzes only the data provided in its input DataFrame.
- It does not modify, clean, or repair raw trip records.
- It does not retrieve additional station information from GBFS.
- It does not determine whether an unusual trip is definitely fraudulent or erroneous. It only identifies records matching the implemented conditions.
- The 3-hour and 2-minute thresholds are analysis rules, not definitive proof of an invalid trip.
- Calendar month distributions combine records from different years.
- Anomaly counters are independent and may overlap; they should not necessarily be added together to obtain a total number of anomalous trips.

`ANALYZE_RAW` is independent of the `PROCESS` step. Its purpose is to provide descriptive information about the raw dataset without changing the data used by subsequent pipeline stages.