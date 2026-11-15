import pandas as pd

from bikeflow.contracts.raw_trips import RawTrip, read_raw_trips_csv
from bikeflow.steps.process import (
    _aggregate_hourly_trips,
    _complete_hourly_grid,
    _deduplicate_trips,
    _filter_network_trips,
    _prepare_trip_times,
    _build_eligible_hours,
    _build_source_periods,
    _build_excluded_hours,
    _build_coverage_periods,
    _add_time_features,
    _add_holiday_feature,
    _join_weather,
    ProcessService,
)
import pytest
from datetime import date
import io
from bikeflow.contracts.process import ProcessInput, ProcessResult
from tests.test_raw_trips import CSV
import logging

def test_filter_network_trips():
    """Check region filtering, missing stations and input preservation."""
    trips = pd.DataFrame(
        [
            ("a", "JC001", "HB001"),
            ("b", "JC001", "6578.01"),
            ("c", "6578.01", "HB001"),
            ("d", "6578.01", "6224.06"),
            ("e", "JC001", None),
            ("f", None, "HB001"),
            ("g", None, None),
            ("h", "6578.01", None),
            ("i", None, "6578.01"),
        ],
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
        ],
    )
    original = trips.copy(deep=True)

    filtered, dropped = _filter_network_trips(trips)

    assert filtered[RawTrip.RIDE_ID].tolist() == ["a", "b", "c", "e", "f"]
    assert dropped == {
        "outside_network": 3,
        "missing_both_stations": 1,
    }
    by_id = filtered.set_index(RawTrip.RIDE_ID)

    assert pd.isna(by_id.loc["b", RawTrip.END_STATION_ID])
    assert pd.isna(by_id.loc["c", RawTrip.START_STATION_ID])
    pd.testing.assert_frame_equal(trips, original)

def test_deduplicate_trips():
    """Check exact duplicates, conflicting IDs and input preservation."""
    trips = pd.DataFrame(
        [
            ("a", "JC001", "HB001"),
            ("a", "JC001", "HB001"),
            ("b", "JC002", "HB002"),
            ("b", "JC002", "HB002"),
            ("b", "JC002", "6578.01"),
            ("c", "JC003", None),
            ("c", "JC003", None),
            ("d", "JC001", "HB001"),
        ],
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
        ],
    )
    original = trips.copy(deep=True)

    cleaned, rejected, dropped = _deduplicate_trips(trips)

    assert rejected[RawTrip.RIDE_ID].tolist() == ["b", "b", "b"]

    pd.testing.assert_frame_equal(
        rejected,
        trips.loc[trips[RawTrip.RIDE_ID].eq("b")],
    )
    # a та c залишаються по одному; b конфліктує; d — окрема поїздка.
    assert cleaned[RawTrip.RIDE_ID].tolist() == ["a", "c", "d"]
    assert dropped == {
        "conflicting_ride_id": 3,
        "duplicate_rows": 2,
    }

    assert len(trips) == len(cleaned) + sum(dropped.values())
    pd.testing.assert_frame_equal(trips, original)


def test_deduplicate_trips_empty():
    """Accept an empty input without reporting removed rows."""
    trips = pd.DataFrame(
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
        ],
    )

    cleaned, rejected, dropped = _deduplicate_trips(trips)

    assert rejected.empty
    assert cleaned.empty
    assert dropped == {
        "conflicting_ride_id": 0,
        "duplicate_rows": 0,
    }

def test_prepare_trip_times():
    """Check UTC conversion, DST, invalid durations and input preservation."""
    trips = pd.DataFrame(
        [
            ("a", "2026-01-05 08:00", "2026-01-05 08:30"),
            ("b", "2026-03-08 01:30", "2026-03-08 03:30"),
            ("c", "2026-11-01 00:30", "2026-11-01 02:30"),
            ("d", "2026-01-05 09:00", "2026-01-05 08:00"),
            ("e", "2026-01-05 09:00", "2026-01-05 09:00"),
            ("f", "2026-11-01 01:10", "2026-11-01 01:40"),
            ("g", "2026-03-08 02:10", "2026-03-08 03:10"),
            ("h", "2026-01-05 08:00", "2026-01-06 09:00"),
        ],
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.STARTED_AT,
            RawTrip.ENDED_AT,
        ],
    )

    for column in (RawTrip.STARTED_AT, RawTrip.ENDED_AT):
        trips[column] = pd.to_datetime(trips[column])

    original = trips.copy(deep=True)

    cleaned, rejected, dropped = _prepare_trip_times(trips)

    assert cleaned[RawTrip.RIDE_ID].tolist() == ["a", "b", "c", "h"]
    assert rejected[RawTrip.RIDE_ID].tolist() == ["d", "e", "f", "g"]
    assert dropped == {
        "invalid_local_time": 2,
        "non_positive_duration": 2,
    }

    # Узимку 08:00 у Нью-Йорку відповідає 13:00 UTC.
    assert cleaned[RawTrip.STARTED_AT].iloc[0] == pd.Timestamp(
        "2026-01-05 13:00", tz="UTC",
    )

    duration_hours = (
        cleaned[RawTrip.ENDED_AT] - cleaned[RawTrip.STARTED_AT]
    ).dt.total_seconds() / 3600

    # b і c перетинають переведення годинника; h — довга поїздка.
    assert duration_hours.tolist() == [0.5, 1.0, 3.0, 25.0]

    assert rejected[RawTrip.STARTED_AT].dt.tz is None
    assert len(trips) == len(cleaned) + sum(dropped.values())
    pd.testing.assert_frame_equal(trips, original)


def test_prepare_trip_times_empty():
    """Accept an empty table with datetime columns."""
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.Series(dtype="datetime64[ns]"),
            RawTrip.ENDED_AT: pd.Series(dtype="datetime64[ns]"),
        },
    )

    cleaned, rejected, dropped = _prepare_trip_times(trips)

    assert cleaned.empty
    assert rejected.empty
    assert dropped == {
        "invalid_local_time": 0,
        "non_positive_duration": 0,
    }

def test_aggregate_hourly_trips():
    """Count each endpoint in its own hour without changing the input."""
    trips = pd.DataFrame(
        [
            ("JC001", "HB001", "2026-01-05 13:50", "2026-01-05 14:10"),
            ("JC001", "JC001", "2026-01-05 13:10", "2026-01-05 13:40"),
            ("HB001", None, "2026-01-05 13:05", "2026-01-05 13:25"),
            (None, "JC001", "2026-01-05 13:15", "2026-01-05 13:45"),
            ("JC002", "HB001", "2026-01-05 14:00", "2026-01-05 14:30"),
        ],
        columns=[
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
            RawTrip.STARTED_AT,
            RawTrip.ENDED_AT,
        ],
    )

    # Ці тестові значення вже задані в UTC.
    for column in (RawTrip.STARTED_AT, RawTrip.ENDED_AT):
        trips[column] = pd.to_datetime(trips[column], utc=True)

    original = trips.copy(deep=True)

    hourly = _aggregate_hourly_trips(trips)

    hour_13 = pd.Timestamp("2026-01-05 13:00", tz="UTC")
    hour_14 = pd.Timestamp("2026-01-05 14:00", tz="UTC")

    assert list(hourly.itertuples(index=False, name=None)) == [
        ("HB001", hour_13, 1, 0),
        ("HB001", hour_14, 0, 2),
        ("JC001", hour_13, 2, 2),
        ("JC002", hour_14, 1, 0),
    ]

    assert hourly["departures"].sum() == 4
    assert hourly["arrivals"].sum() == 4
    assert not hourly.duplicated(["station_id", "timestamp"]).any()

    pd.testing.assert_frame_equal(trips, original)

def test_aggregate_hourly_trips_empty():
    """Return an empty table with the expected columns and count types."""
    trips = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: pd.Series(dtype="string"),
            RawTrip.END_STATION_ID: pd.Series(dtype="string"),
            RawTrip.STARTED_AT: pd.Series(dtype="datetime64[ns, UTC]"),
            RawTrip.ENDED_AT: pd.Series(dtype="datetime64[ns, UTC]"),
        },
    )

    hourly = _aggregate_hourly_trips(trips)

    assert hourly.empty
    assert hourly.columns.tolist() == [
        "station_id",
        "timestamp",
        "departures",
        "arrivals",
    ]
    assert hourly["departures"].dtype == "int64"
    assert hourly["arrivals"].dtype == "int64"

def test_complete_hourly_grid():
    """Add eligible zero hours, exclude other hours and preserve inputs."""
    hour_13 = pd.Timestamp("2026-01-05 13:00", tz="UTC")
    hour_14 = hour_13 + pd.Timedelta(hours=1)
    hour_15 = hour_14 + pd.Timedelta(hours=1)

    hourly = pd.DataFrame(
        [
            ("JC001", hour_13, 2, 1),
            ("JC001", hour_15, 7, 3),
        ],
        columns=["station_id", "timestamp", "departures", "arrivals"],
    )
    eligible = pd.DataFrame(
        [
            ("JC001", hour_13),
            ("JC001", hour_14),
            ("HB001", hour_14),
        ],
        columns=["station_id", "timestamp"],
    )

    original_hourly = hourly.copy(deep=True)
    original_eligible = eligible.copy(deep=True)

    result = _complete_hourly_grid(hourly, eligible)

    assert list(result.itertuples(index=False, name=None)) == [
        ("HB001", hour_14, 0, 0),
        ("JC001", hour_13, 2, 1),
        ("JC001", hour_14, 0, 0),
    ]

    pd.testing.assert_frame_equal(hourly, original_hourly)
    pd.testing.assert_frame_equal(eligible, original_eligible)

    # Немає подій, але є підтверджені години — отримуємо нулі.
    zero_result = _complete_hourly_grid(hourly.iloc[:0], eligible)
    assert len(zero_result) == 3
    assert zero_result[["departures", "arrivals"]].to_numpy().sum() == 0

    # Немає дозволених годин — немає навчальних рядків.
    empty_result = _complete_hourly_grid(hourly, eligible.iloc[:0])
    assert empty_result.empty


def test_complete_hourly_grid_rejects_duplicate_keys():
    """Reject duplicate station-hours in either input."""
    hourly = pd.DataFrame(
        [
            ("JC001", pd.Timestamp("2026-01-05 13:00", tz="UTC"), 2, 1),
        ],
        columns=["station_id", "timestamp", "departures", "arrivals"],
    )
    eligible = hourly[["station_id", "timestamp"]].copy()

    duplicate_grid = pd.concat([eligible, eligible], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate station-hour"):
        _complete_hourly_grid(hourly, duplicate_grid)

    duplicate_counts = pd.concat([hourly, hourly], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate station-hour"):
        _complete_hourly_grid(duplicate_counts, eligible)

def test_build_eligible_hours():
    """Keep full hours, merge overlaps, apply exclusions and preserve inputs."""
    periods = pd.DataFrame(
        [
            ("JC001", "2026-01-05 13:20", "2026-01-05 16:40"),
            ("JC001", "2026-01-05 14:00", "2026-01-05 15:00"),
            ("HB001", "2026-01-05 14:00", "2026-01-05 16:00"),
        ],
        columns=["station_id", "period_start", "period_end"],
    )

    for column in ("period_start", "period_end"):
        periods[column] = pd.to_datetime(periods[column], utc=True)

    hour_14 = pd.Timestamp("2026-01-05 14:00", tz="UTC")
    hour_15 = hour_14 + pd.Timedelta(hours=1)

    excluded = pd.DataFrame(
        [("JC001", hour_15)],
        columns=["station_id", "timestamp"],
    )

    original_periods = periods.copy(deep=True)
    original_excluded = excluded.copy(deep=True)

    grid = _build_eligible_hours(periods, excluded)

    assert list(grid.itertuples(index=False, name=None)) == [
        ("HB001", hour_14),
        ("HB001", hour_15),
        ("JC001", hour_14),
    ]
    assert not grid.duplicated(["station_id", "timestamp"]).any()

    pd.testing.assert_frame_equal(periods, original_periods)
    pd.testing.assert_frame_equal(excluded, original_excluded)

    # Інтервал не містить жодної повної календарної години.
    partial = periods.iloc[[0]].copy()
    partial["period_start"] = pd.Timestamp("2026-01-05 13:20", tz="UTC")
    partial["period_end"] = pd.Timestamp("2026-01-05 14:20", tz="UTC")
    assert _build_eligible_hours(partial).empty

    assert _build_eligible_hours(periods.iloc[:0]).empty


def test_build_eligible_hours_rejects_invalid_periods():
    """Reject invalid interval bounds and timestamps without a timezone."""
    timestamp = pd.Timestamp("2026-01-05 13:00", tz="UTC")

    periods = pd.DataFrame(
        [("JC001", timestamp, timestamp)],
        columns=["station_id", "period_start", "period_end"],
    )

    with pytest.raises(ValueError, match="must precede"):
        _build_eligible_hours(periods)

    periods["period_end"] = timestamp + pd.Timedelta(hours=1)
    periods["period_start"] = periods["period_start"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="UTC"):
        _build_eligible_hours(periods)

def test_build_source_periods():
    """Merge adjacent months, remove duplicates and preserve missing months."""
    result = _build_source_periods(
        (
            date(2026, 4, 1),
            date(2026, 1, 1),
            date(2026, 3, 1),
            date(2026, 1, 1),
        ),
    )

    assert list(result.itertuples(index=False, name=None)) == [
        (
            pd.Timestamp("2026-01-01 05:00", tz="UTC"),
            pd.Timestamp("2026-02-01 05:00", tz="UTC"),
        ),
        (
            pd.Timestamp("2026-03-01 05:00", tz="UTC"),
            pd.Timestamp("2026-05-01 04:00", tz="UTC"),
        ),
    ]


def test_build_source_periods_dst():
    """Respect the lost spring hour and repeated autumn hour."""
    result = _build_source_periods(
        (date(2026, 3, 1), date(2026, 11, 1)),
    )

    duration_hours = (
        result["period_end"] - result["period_start"]
    ).dt.total_seconds() / 3600

    # Березень: 31 × 24 − 1. Листопад: 30 × 24 + 1.
    assert duration_hours.tolist() == [743.0, 721.0]


def test_build_source_periods_empty():
    """Return an empty table with UTC datetime columns."""
    result = _build_source_periods(())

    assert result.empty
    assert result.columns.tolist() == ["period_start", "period_end"]
    assert str(result["period_start"].dt.tz) == "UTC"
    assert str(result["period_end"].dt.tz) == "UTC"


def test_build_source_periods_rejects_invalid_months():
    """Require actual dates identifying the first day of a month."""
    for value in (
        date(2026, 1, 2),
        "2026-01",
        None,
        pd.Timestamp("2026-01-01"),
    ):
        with pytest.raises(ValueError, match="first day"):
            _build_source_periods((value,))

def test_build_excluded_hours():
    """Mark both endpoints of rejected trips, deduplicate and preserve input."""
    rejected = pd.DataFrame(
        [
            (" JC001 ", "HB001", "2026-01-05 09:10", "2026-01-05 08:20"),
            ("JC001", "JC001", "2026-01-05 09:30", "2026-01-05 09:30"),
            (None, "HB002", "2026-01-05 10:00", "2026-01-05 09:50"),
            ("6578.01", None, "2026-01-05 10:00", "2026-01-05 09:50"),
        ],
        columns=[
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
            RawTrip.STARTED_AT,
            RawTrip.ENDED_AT,
        ],
    )
    for column in (RawTrip.STARTED_AT, RawTrip.ENDED_AT):
        rejected[column] = pd.to_datetime(rejected[column])

    original = rejected.copy(deep=True)

    result = _build_excluded_hours(rejected)

    assert list(result.itertuples(index=False, name=None)) == [
        ("HB001", pd.Timestamp("2026-01-05 13:00", tz="UTC")),
        ("HB002", pd.Timestamp("2026-01-05 14:00", tz="UTC")),
        ("JC001", pd.Timestamp("2026-01-05 14:00", tz="UTC")),
    ]
    pd.testing.assert_frame_equal(rejected, original)


def test_build_excluded_hours_ambiguous():
    """Exclude both occurrences of the repeated autumn hour."""
    rejected = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: ["JC001"],
            RawTrip.END_STATION_ID: ["JC001"],
            RawTrip.STARTED_AT: pd.to_datetime(["2026-11-01 01:10"]),
            RawTrip.ENDED_AT: pd.to_datetime(["2026-11-01 01:40"]),
        },
    )

    result = _build_excluded_hours(rejected)

    assert list(result.itertuples(index=False, name=None)) == [
        ("JC001", pd.Timestamp("2026-11-01 05:00", tz="UTC")),
        ("JC001", pd.Timestamp("2026-11-01 06:00", tz="UTC")),
    ]


def test_build_excluded_hours_nonexistent():
    """Exclude the local spring day for its station and mark the other endpoint."""
    rejected = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: ["JC001"],
            RawTrip.END_STATION_ID: ["HB001"],
            RawTrip.STARTED_AT: pd.to_datetime(["2026-03-08 02:10"]),
            RawTrip.ENDED_AT: pd.to_datetime(["2026-03-08 03:10"]),
        },
    )

    result = _build_excluded_hours(rejected)
    station_hours = result.loc[result["station_id"].eq("JC001"), "timestamp"]

    assert station_hours.tolist() == pd.date_range(
        "2026-03-08 05:00", "2026-03-09 04:00",
        freq="h", inclusive="left", tz="UTC",
    ).tolist()

    # Цей місцевий день має 23 години через перехід на літній час.
    assert len(station_hours) == 23

    assert list(
        result.loc[result["station_id"].eq("HB001")].itertuples(
            index=False, name=None,
        )
    ) == [("HB001", pd.Timestamp("2026-03-08 07:00", tz="UTC"))]


def test_build_excluded_hours_empty():
    """Return a typed empty table when there are no rejected trips."""
    rejected = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: pd.Series(dtype="string"),
            RawTrip.END_STATION_ID: pd.Series(dtype="string"),
            RawTrip.STARTED_AT: pd.Series(dtype="datetime64[ns]"),
            RawTrip.ENDED_AT: pd.Series(dtype="datetime64[ns]"),
        },
    )

    result = _build_excluded_hours(rejected)

    assert result.empty
    assert result.columns.tolist() == ["station_id", "timestamp"]
    assert str(result["timestamp"].dt.tz) == "UTC"


def test_build_excluded_hours_rejects_unusable_times():
    """Do not silently skip a missing time or accept already localized input."""
    rejected = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: ["JC001"],
            RawTrip.END_STATION_ID: [None],
            RawTrip.STARTED_AT: pd.to_datetime([None]),
            RawTrip.ENDED_AT: pd.to_datetime(["2026-01-05 09:00"]),
        },
    )

    with pytest.raises(ValueError, match="missing local time"):
        _build_excluded_hours(rejected)

    rejected[RawTrip.STARTED_AT] = pd.to_datetime(
        ["2026-01-05 08:00"], utc=True,
    )
    with pytest.raises(ValueError, match="timezone-naive"):
        _build_excluded_hours(rejected)

def test_conflicting_trips_exclude_all_recorded_hours():
    """Mark all conflict versions, including a version with an outside endpoint."""
    trips = pd.DataFrame(
        [
            ("a", "JC010", "HB010", "2026-01-05 06:10", "2026-01-05 06:30"),
            ("a", "JC010", "HB010", "2026-01-05 06:10", "2026-01-05 06:30"),
            ("b", "JC001", "HB001", "2026-01-05 08:10", "2026-01-05 08:30"),
            ("b", "JC001", "HB001", "2026-01-05 08:10", "2026-01-05 08:30"),
            ("b", "JC002", "6578.01", "2026-01-05 09:10", "2026-01-05 09:30"),
            ("d", "JC001", "HB001", "2026-01-05 08:20", "2026-01-05 08:40"),
        ],
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
            RawTrip.STARTED_AT,
            RawTrip.ENDED_AT,
        ],
    )
    for column in (RawTrip.STARTED_AT, RawTrip.ENDED_AT):
        trips[column] = pd.to_datetime(trips[column])

    cleaned, rejected, dropped = _deduplicate_trips(trips)
    excluded = _build_excluded_hours(rejected)

    assert cleaned[RawTrip.RIDE_ID].tolist() == ["a", "d"]
    assert rejected[RawTrip.RIDE_ID].tolist() == ["b", "b", "b"]
    assert rejected[RawTrip.STARTED_AT].dt.tz is None

    assert dropped == {
        "conflicting_ride_id": 3,
        "duplicate_rows": 1,
    }

    assert list(excluded.itertuples(index=False, name=None)) == [
        ("HB001", pd.Timestamp("2026-01-05 13:00", tz="UTC")),
        ("JC001", pd.Timestamp("2026-01-05 13:00", tz="UTC")),
        ("JC002", pd.Timestamp("2026-01-05 14:00", tz="UTC")),
    ]


def test_exact_duplicates_do_not_exclude_hours():
    """Keep one event-bearing trip without creating a coverage exclusion."""
    trip = pd.DataFrame(
        {
            RawTrip.RIDE_ID: ["a"],
            RawTrip.START_STATION_ID: ["JC001"],
            RawTrip.END_STATION_ID: ["HB001"],
            RawTrip.STARTED_AT: pd.to_datetime(["2026-01-05 08:10"]),
            RawTrip.ENDED_AT: pd.to_datetime(["2026-01-05 08:30"]),
        },
    )
    trips = pd.concat([trip, trip], ignore_index=True)

    cleaned, rejected, dropped = _deduplicate_trips(trips)

    pd.testing.assert_frame_equal(cleaned, trip)
    assert rejected.empty
    assert dropped == {
        "conflicting_ride_id": 0,
        "duplicate_rows": 1,
    }
    assert _build_excluded_hours(rejected).empty

def test_build_coverage_periods():
    """Trim each continuous block, retain long trips and preserve inputs."""
    source = _build_source_periods(
        (date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1)),
    )
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.to_datetime(
                ["2026-01-15 08:00"], utc=True,
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                ["2026-01-16 09:00"], utc=True,
            ),
        },
    )
    original_source = source.copy(deep=True)
    original_trips = trips.copy(deep=True)

    result = _build_coverage_periods(source, trips)

    assert list(result.itertuples(index=False, name=None)) == [
        (
            pd.Timestamp("2026-01-01 05:00", tz="UTC"),
            pd.Timestamp("2026-02-27 05:00", tz="UTC"),
        ),
        (
            pd.Timestamp("2026-04-01 04:00", tz="UTC"),
            pd.Timestamp("2026-04-29 04:00", tz="UTC"),
        ),
    ]

    # Поїздка тривалістю 25 годин залишається незмінною.
    pd.testing.assert_frame_equal(source, original_source)
    pd.testing.assert_frame_equal(trips, original_trips)


def test_build_coverage_periods_longer_than_buffer(caplog):
    """Warn once about long trips, keep inputs and respect the chosen buffer."""
    source = _build_source_periods((date(2026, 1, 1),))
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.to_datetime([
                "2026-01-01 05:00", "2026-01-05 05:00", "2026-01-10 05:00",
            ], utc=True),
            RawTrip.ENDED_AT: pd.to_datetime([
                "2026-01-03 06:00", "2026-01-07 07:00", "2026-01-12 05:00",
            ], utc=True),
        },
    )
    original_source = source.copy(deep=True)
    original_trips = trips.copy(deep=True)

    with caplog.at_level(logging.WARNING, logger="bikeflow.steps.process"):
        result = _build_coverage_periods(source, trips)

    assert result["period_end"].tolist() == [
        pd.Timestamp("2026-01-30 05:00", tz="UTC"),
    ]
    messages = [
        record.getMessage() for record in caplog.records
        if record.name == "bikeflow.steps.process"
    ]
    assert len(messages) == 1
    assert "2 trip(s)" in messages[0]
    assert "departure_buffer=2 days 00:00:00" in messages[0]
    assert "maximum duration=2 days 02:00:00" in messages[0]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="bikeflow.steps.process"):
        result = _build_coverage_periods(
            source, trips, departure_buffer=pd.Timedelta(hours=72),
        )

    assert result["period_end"].tolist() == [
        pd.Timestamp("2026-01-29 05:00", tz="UTC"),
    ]
    assert not any(
        record.name == "bikeflow.steps.process"
        for record in caplog.records
    )
    pd.testing.assert_frame_equal(source, original_source)
    pd.testing.assert_frame_equal(trips, original_trips)


def test_build_coverage_periods_empty_and_short():
    """Handle no events, no source intervals and a block consumed by the buffer."""
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.Series(dtype="datetime64[ns, UTC]"),
            RawTrip.ENDED_AT: pd.Series(dtype="datetime64[ns, UTC]"),
        },
    )
    source = _build_source_periods((date(2026, 1, 1),))

    # Відсутність поїздок сама по собі не скасовує покриття джерелом.
    assert len(_build_coverage_periods(source, trips)) == 1

    short = source.copy()
    short["period_end"] = short["period_start"] + pd.Timedelta(hours=48)

    for periods in (source.iloc[:0], short):
        result = _build_coverage_periods(periods, trips)

        assert result.empty
        assert result.columns.tolist() == ["period_start", "period_end"]
        assert str(result["period_start"].dt.tz) == "UTC"
        assert str(result["period_end"].dt.tz) == "UTC"


def test_build_coverage_periods_rejects_invalid_inputs():
    """Require a positive buffer, UTC, valid durations and merged intervals."""
    source = _build_source_periods((date(2026, 1, 1),))
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.to_datetime(
                ["2026-01-05 08:00"], utc=True,
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                ["2026-01-05 09:00"], utc=True,
            ),
        },
    )

    for buffer in (pd.Timedelta(0), pd.Timedelta(hours=-1), pd.NaT, 48):
        with pytest.raises(ValueError, match="positive pandas Timedelta"):
            _build_coverage_periods(
                source, trips, departure_buffer=buffer,
            )

    naive = source.copy()
    naive["period_start"] = naive["period_start"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="UTC"):
        _build_coverage_periods(naive, trips)

    overlapping = pd.concat([source, source], ignore_index=True)

    with pytest.raises(ValueError, match="merge overlapping or adjacent"):
        _build_coverage_periods(overlapping, trips)

    invalid_trips = trips.copy()
    invalid_trips[RawTrip.ENDED_AT] = invalid_trips[RawTrip.STARTED_AT]

    with pytest.raises(ValueError, match="positive durations"):
        _build_coverage_periods(source, invalid_trips)

def test_add_time_features():
    """Use local calendar values and preserve all original data and ordering."""
    hourly = pd.DataFrame(
        {
            "station_id": ["JC001"] * 5,
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 13:00",
                    "2026-07-06 13:00",
                    "2026-01-04 15:00",
                    "2026-01-04 02:00",
                    "2026-01-01 02:00",
                ],
                utc=True,
            ),
            "departures": [2, 0, 1, 5, 0],
            "arrivals": [1, 0, 2, 1, 0],
        },
        index=[10, 20, 30, 40, 50],
    )
    original = hourly.copy(deep=True)

    result = _add_time_features(hourly)

    features = ["hour", "weekday", "month", "is_weekend"]

    assert list(result[features].itertuples(index=False, name=None)) == [
        (8, 0, 1, False),
        (9, 0, 7, False),
        (10, 6, 1, True),
        (21, 5, 1, True),
        (21, 2, 12, False),
    ]

    assert result.columns.tolist() == hourly.columns.tolist() + features

    pd.testing.assert_frame_equal(result[hourly.columns], original)
    pd.testing.assert_frame_equal(hourly, original)


def test_add_time_features_dst():
    """Skip the spring hour and preserve both occurrences of the autumn hour."""
    hourly = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-03-08 06:00",
                    "2026-03-08 07:00",
                    "2026-11-01 05:00",
                    "2026-11-01 06:00",
                ],
                utc=True,
            ),
        },
    )

    result = _add_time_features(hourly)

    assert result["hour"].tolist() == [1, 3, 1, 1]
    assert result["weekday"].tolist() == [6, 6, 6, 6]
    assert result["month"].tolist() == [3, 3, 11, 11]
    assert result["is_weekend"].all()

    # Обидві осінні години залишаються окремими рядками.
    assert len(result) == 4
    pd.testing.assert_series_equal(
        result["timestamp"],
        hourly["timestamp"],
    )


def test_add_time_features_empty():
    """Return typed feature columns even when no station-hours remain."""
    hourly = pd.DataFrame(
        {"timestamp": pd.Series(dtype="datetime64[ns, UTC]")},
    )

    result = _add_time_features(hourly)

    assert result.empty
    assert result.columns.tolist() == [
        "timestamp", "hour", "weekday", "month", "is_weekend",
    ]

    for column in ("hour", "weekday", "month"):
        assert result[column].dtype == "int8"

    assert result["is_weekend"].dtype == "bool"
    assert str(result["timestamp"].dt.tz) == "UTC"


def test_add_time_features_rejects_invalid_input():
    """Reject unusable timestamps and accidental overwriting of features."""
    valid = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-05 13:00"], utc=True)},
    )

    naive = valid.assign(
        timestamp=valid["timestamp"].dt.tz_localize(None),
    )
    local = valid.assign(
        timestamp=valid["timestamp"].dt.tz_convert("America/New_York"),
    )
    partial = valid.assign(
        timestamp=valid["timestamp"] + pd.Timedelta(minutes=1),
    )
    missing = valid.copy()
    missing.loc[0, "timestamp"] = pd.NaT

    for frame, message in (
        (valid.drop(columns="timestamp"), "missing timestamp"),
        (naive, "UTC"),
        (local, "UTC"),
        (missing, "UTC"),
        (partial, "hour starts"),
        (valid.assign(hour=8), "already present"),
    ):
        with pytest.raises(ValueError, match=message):
            _add_time_features(frame)

def test_add_holiday_feature():
    """Mark actual and observed holidays by local date and preserve prior features."""
    hourly = _add_time_features(
        pd.DataFrame(
            {
                "station_id": ["JC001"] * 7,
                "timestamp": pd.to_datetime(
                    [
                        "2026-07-03 16:00",
                        "2026-07-04 16:00",
                        "2026-07-06 16:00",
                        "2026-01-01 04:00",
                        "2026-01-01 05:00",
                        "2026-01-02 04:00",
                        "2026-01-02 05:00",
                    ],
                    utc=True,
                ),
                "departures": [1] * 7,
                "arrivals": [0] * 7,
            },
            index=[10, 20, 30, 40, 50, 60, 70],
        ),
    )
    original = hourly.copy(deep=True)

    result = _add_holiday_feature(hourly)

    assert result["is_holiday"].tolist() == [
        True, True, False, False, True, True, False,
    ]

    # Перенесений вихідний у п'ятницю та фактичне свято в суботу.
    assert result["is_weekend"].tolist()[:3] == [False, True, False]

    assert result["is_holiday"].dtype == "bool"
    assert result.columns.tolist() == hourly.columns.tolist() + ["is_holiday"]

    pd.testing.assert_frame_equal(result[hourly.columns], original)
    pd.testing.assert_frame_equal(hourly, original)


def test_add_holiday_feature_cross_year():
    """Include a next-year holiday observed in the current local year."""
    hourly = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2021-12-31 02:00", "2022-01-01 02:00"],
                utc=True,
            ),
        },
    )

    result = _add_holiday_feature(hourly)

    # Місцеві дати: 30 та 31 грудня 2021 року.
    # 31 грудня — перенесений вихідний для Нового року.
    assert result["is_holiday"].tolist() == [False, True]


def test_add_holiday_feature_empty():
    """Keep a boolean holiday column in an empty result."""
    hourly = pd.DataFrame(
        {"timestamp": pd.Series(dtype="datetime64[ns, UTC]")},
    )

    result = _add_holiday_feature(hourly)

    assert result.empty
    assert result.columns.tolist() == ["timestamp", "is_holiday"]
    assert result["is_holiday"].dtype == "bool"
    assert str(result["timestamp"].dt.tz) == "UTC"


def test_add_holiday_feature_rejects_invalid_input():
    """Require UTC hour starts and prevent overwriting an existing flag."""
    valid = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-05 13:00"], utc=True)},
    )

    naive = valid.assign(
        timestamp=valid["timestamp"].dt.tz_localize(None),
    )
    local = valid.assign(
        timestamp=valid["timestamp"].dt.tz_convert("America/New_York"),
    )
    partial = valid.assign(
        timestamp=valid["timestamp"] + pd.Timedelta(minutes=1),
    )
    missing = valid.copy()
    missing.loc[0, "timestamp"] = pd.NaT

    for frame, message in (
        (valid.drop(columns="timestamp"), "missing timestamp"),
        (naive, "UTC"),
        (local, "UTC"),
        (missing, "UTC"),
        (partial, "hour starts"),
        (valid.assign(is_holiday=False), "already present"),
    ):
        with pytest.raises(ValueError, match=message):
            _add_holiday_feature(frame)

def test_join_weather():
    """Share weather by hour, keep real zeros and exclude incomplete rows."""
    hours = pd.date_range(
        "2026-01-05 13:00", periods=5, freq="h", tz="UTC",
    )
    hourly = pd.DataFrame(
        [
            ("HB001", hours[0], 0, 0),
            ("JC001", hours[0], 2, 1),
            ("HB001", hours[1], 1, 2),
            ("JC001", hours[1], 0, 1),
            ("JC001", hours[2], 1, 0),
            ("JC001", hours[3], 3, 2),
        ],
        columns=["station_id", "timestamp", "departures", "arrivals"],
        index=[10, 20, 30, 40, 50, 60],
    )
    weather = pd.DataFrame(
        [
            (hours[3], -3.0, 0.0, 0.0),
            (hours[0], 5.0, 0.5, 2.0),
            (hours[1], float("nan"), 1.0, 3.0),
            (hours[4], 8.0, 4.0, 5.0),
        ],
        columns=[
            "timestamp", "temperature_c", "precipitation_mm", "wind_speed_ms",
        ],
    )
    original_hourly = hourly.copy(deep=True)
    original_weather = weather.copy(deep=True)

    result, excluded = _join_weather(hourly, weather)

    # Два рядки за годину з NaN та один за годину без погодного запису.
    assert excluded == 3
    assert len(hourly) == len(result) + excluded

    pd.testing.assert_frame_equal(
        result[hourly.columns],
        hourly.iloc[[0, 1, 5]].reset_index(drop=True),
    )

    assert result["temperature_c"].tolist() == [5.0, 5.0, -3.0]
    assert result["precipitation_mm"].tolist() == [0.5, 0.5, 0.0]
    assert result["wind_speed_ms"].tolist() == [2.0, 2.0, 0.0]

    pd.testing.assert_frame_equal(hourly, original_hourly)
    pd.testing.assert_frame_equal(weather, original_weather)


def test_join_weather_empty():
    """Handle an empty grid or absent weather without inventing values."""
    timestamp = pd.Timestamp("2026-01-05 13:00", tz="UTC")
    hourly = pd.DataFrame(
        [("JC001", timestamp, 0, 0)],
        columns=["station_id", "timestamp", "departures", "arrivals"],
    )
    weather = pd.DataFrame(
        [(timestamp, 5.0, 0.0, 2.0)],
        columns=[
            "timestamp", "temperature_c", "precipitation_mm", "wind_speed_ms",
        ],
    )

    result, excluded = _join_weather(hourly.iloc[:0], weather)

    assert result.empty
    assert excluded == 0
    assert result["departures"].dtype == "int64"
    assert "temperature_c" in result.columns

    result, excluded = _join_weather(hourly, weather.iloc[:0])

    assert result.empty
    assert excluded == 1


def test_join_weather_rejects_invalid_inputs():
    """Prevent duplicate keys, wrong time zones and feature overwriting."""
    timestamp = pd.Timestamp("2026-01-05 13:00", tz="UTC")
    hourly = pd.DataFrame(
        [("JC001", timestamp)],
        columns=["station_id", "timestamp"],
    )
    weather = pd.DataFrame(
        [(timestamp, 5.0, 0.0, 2.0)],
        columns=[
            "timestamp", "temperature_c", "precipitation_mm", "wind_speed_ms",
        ],
    )
    duplicate = pd.concat([hourly, hourly], ignore_index=True)
    naive = hourly.assign(
        timestamp=hourly["timestamp"].dt.tz_localize(None),
    )

    for frame, message in (
        (duplicate, "duplicate station-hour"),
        (naive, "UTC"),
        (hourly.assign(temperature_c=5.0), "already present"),
    ):
        with pytest.raises(ValueError, match=message):
            _join_weather(frame, weather)

    with pytest.raises(ValueError, match="duplicate timestamps"):
        _join_weather(
            hourly,
            pd.concat([weather, weather], ignore_index=True),
        )

def _process_test_trips(rows):
    """Build full-schema raw trips using the existing CSV fixture as a template."""
    template = read_raw_trips_csv(io.StringIO(CSV))
    trips = template.iloc[[0] * len(rows)].reset_index(drop=True).copy()

    values = pd.DataFrame(
        rows,
        columns=[
            RawTrip.RIDE_ID,
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
            RawTrip.STARTED_AT,
            RawTrip.ENDED_AT,
        ],
    )

    for column in (
        RawTrip.RIDE_ID,
        RawTrip.START_STATION_ID,
        RawTrip.END_STATION_ID,
    ):
        trips[column] = values[column].astype("string")

    for column in (RawTrip.STARTED_AT, RawTrip.ENDED_AT):
        trips[column] = pd.to_datetime(values[column])

    return trips


def _process_test_weather(timestamps):
    """Build prepared weather covering just the requested UTC hours."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps, utc=True),
            "temperature_c": 5.0,
            "precipitation_mm": 0.0,
            "wind_speed_ms": 2.0,
        },
    )

def test_process_service():
    """Run cleaning, exclusions, zeros, features and weather as one pipeline."""
    trips = _process_test_trips(
        [
            ("a", "JC001", "HB001", "2026-01-05 08:10", "2026-01-05 08:30"),
            ("a", "JC001", "HB001", "2026-01-05 08:10", "2026-01-05 08:30"),
            ("b", "JC001", "HB001", "2026-01-05 09:10", "2026-01-05 09:30"),
            ("b", "JC001", "HB001", "2026-01-05 10:10", "2026-01-05 10:30"),
            ("c", "JC001", "HB001", "2026-01-05 11:30", "2026-01-05 11:10"),
            ("d", "JC001", "6578.01", "2026-01-05 12:10", "2026-01-05 12:30"),
            ("e", "6578.01", "6224.06", "2026-01-05 08:00", "2026-01-05 08:20"),
            ("f", None, None, "2026-01-05 08:00", "2026-01-05 08:20"),
            ("g", "HB001", "JC001", "2026-01-05 09:15", "2026-01-05 09:45"),
            ("h", "JC002", None, "2026-01-06 08:10", "2026-01-06 08:20"),
            ("i", "6578.01", "HB001", "2026-01-05 12:15", "2026-01-05 12:40"),
        ],
    )
    hours = pd.date_range(
        "2026-01-05 13:00", periods=6, freq="h", tz="UTC",
    )
    weather = _process_test_weather(hours)
    weather.loc[5, "temperature_c"] = float("nan")

    original_trips = trips.copy(deep=True)
    original_weather = weather.copy(deep=True)

    result = ProcessService().process(
        ProcessInput(
            trips=trips,
            loaded_months=(date(2026, 1, 1),),
            weather=weather,
        ),
    )

    assert isinstance(result, ProcessResult)

    targets = result.dataset[
        ["station_id", "timestamp", "departures", "arrivals"]
    ]

    assert list(targets.itertuples(index=False, name=None)) == [
        ("HB001", hours[0], 0, 1),
        ("HB001", hours[4], 0, 1),
        ("JC001", hours[0], 1, 0),
        ("JC001", hours[4], 1, 0),
        ("JC002", hours[0], 0, 0),
        ("JC002", hours[1], 0, 0),
        ("JC002", hours[2], 0, 0),
        ("JC002", hours[3], 0, 0),
        ("JC002", hours[4], 0, 0),
    ]

    assert result.dropped_rows == {
        "conflicting_ride_id": 2,
        "duplicate_rows": 1,
        "outside_network": 1,
        "missing_both_stations": 1,
        "invalid_local_time": 0,
        "non_positive_duration": 1,
    }

    assert result.feature_columns == (
        "station_id",
        "hour",
        "weekday",
        "month",
        "is_weekend",
        "is_holiday",
        "temperature_c",
        "precipitation_mm",
        "wind_speed_ms",
    )
    assert result.dataset["hour"].tolist() == [
        8, 12, 8, 12, 8, 9, 10, 11, 12,
    ]
    assert not result.dataset["is_holiday"].any()

    # 3 станції × 696 годин після буфера,
    # мінус 6 виключень через якість і 9 фінальних рядків.
    assert result.weather_excluded_station_hours == 3 * 696 - 6 - 9
    assert result.departure_buffer_hours == 48

    pd.testing.assert_frame_equal(trips, original_trips)
    pd.testing.assert_frame_equal(weather, original_weather)


def test_process_service_period_boundaries():
    """Keep arrivals from earlier starts and departures before the cutoff."""
    trips = _process_test_trips(
        [
            (
                "early", "JC001", "HB001",
                "2025-12-31 23:50", "2026-01-01 00:10",
            ),
            (
                "late", "JC001", "HB001",
                "2026-01-29 23:50", "2026-01-30 00:10",
            ),
        ],
    )
    weather = _process_test_weather(
        [
            "2026-01-01 04:00",
            "2026-01-01 05:00",
            "2026-01-30 04:00",
            "2026-01-30 05:00",
        ],
    )

    result = ProcessService().process(
        ProcessInput(
            trips=trips,
            loaded_months=(date(2026, 1, 1),),
            weather=weather,
        ),
    )

    first = pd.Timestamp("2026-01-01 05:00", tz="UTC")
    last = pd.Timestamp("2026-01-30 04:00", tz="UTC")
    targets = result.dataset[
        ["station_id", "timestamp", "departures", "arrivals"]
    ]

    assert list(targets.itertuples(index=False, name=None)) == [
        ("HB001", first, 0, 1),
        ("HB001", last, 0, 0),
        ("JC001", first, 0, 0),
        ("JC001", last, 1, 0),
    ]

    assert result.dataset.loc[
        result.dataset["timestamp"].eq(first),
        "is_holiday",
    ].all()


def test_process_service_empty():
    """Return a valid empty result when the input contains no stations."""
    result = ProcessService().process(
        ProcessInput(
            trips=_process_test_trips([]),
            loaded_months=(date(2026, 1, 1),),
            weather=_process_test_weather([]),
        ),
    )

    assert result.dataset.empty
    assert sum(result.dropped_rows.values()) == 0
    assert result.weather_excluded_station_hours == 0

    assert set(result.dataset.columns) == {
        "timestamp",
        *result.feature_columns,
        *result.target_columns,
    }


def test_process_service_uses_configured_buffer(caplog):
    """Count a long trip and keep the buffer boundary despite its warning."""
    trips = _process_test_trips([
        ("long", "JC001", "JC001", "2026-01-05 08:00", "2026-01-07 09:00"),
        ("edge", "JC001", "JC001", "2026-01-29 23:30", "2026-01-30 00:30"),
    ])
    weather = _process_test_weather([
        "2026-01-05 13:00", "2026-01-07 14:00",
        "2026-01-30 04:00", "2026-01-30 05:00",
    ])
    original_trips = trips.copy(deep=True)
    original_weather = weather.copy(deep=True)

    for buffer_hours, expected_departures in ((48, 2), (72, 1)):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="bikeflow.steps.process"):
            result = ProcessService().process(ProcessInput(
                trips=trips,
                loaded_months=(date(2026, 1, 1),),
                weather=weather,
                departure_buffer_hours=buffer_hours,
            ))

        assert result.departure_buffer_hours == buffer_hours
        assert result.dataset["departures"].sum() == expected_departures
        assert result.dataset["arrivals"].sum() == 1
        assert sum(result.dropped_rows.values()) == 0

        messages = [
            record.getMessage() for record in caplog.records
            if record.name == "bikeflow.steps.process"
        ]
        if buffer_hours == 48:
            assert len(messages) == 1
            assert "1 trip(s)" in messages[0]
        else:
            assert messages == []

        pd.testing.assert_frame_equal(trips, original_trips)
        pd.testing.assert_frame_equal(weather, original_weather)