"""Validation built into the step contracts."""

import io

import pytest
from datetime import date

import pandas as pd
from bikeflow import config
from bikeflow.contracts.analyze_raw import AnalyzeRawInput
from bikeflow.contracts.ingest import IngestInput, IngestResult
from bikeflow.contracts.process import ProcessInput, ProcessResult
from bikeflow.contracts.raw_trips import RawTrip, SchemaError, read_raw_trips_csv
from bikeflow.storage.dataset_store import LocalDatasetStore
from tests.test_raw_trips import CSV

@pytest.fixture
def weather_frame():
    """One valid prepared regional weather row."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-09-08 12:00"], utc=True),
            "temperature_c": [20.0],
            "precipitation_mm": [0.0],
            "wind_speed_ms": [2.0],
        },
    )

@pytest.mark.parametrize(
    "contract, kwargs",
    [
        (AnalyzeRawInput, {}),
        (ProcessInput, {"loaded_months": (date(2026, 9, 1),)}),
    ],
)

def test_step_inputs_accept_valid_trips_and_reject_invalid(
    contract, kwargs, weather_frame,
):
    trips = read_raw_trips_csv(io.StringIO(CSV))

    if contract is ProcessInput:
        kwargs = kwargs | {"weather": weather_frame}

    assert contract(trips, **kwargs).trips is trips

    with pytest.raises(SchemaError):
        contract(trips.drop(columns=[RawTrip.RIDE_ID]), **kwargs)


def test_ingest_input_validation(tmp_path):
    store = LocalDatasetStore(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        IngestInput(0, config.TRIPDATA_FILE_REGEX, store)
    with pytest.raises(ValueError, match="month"):
        IngestInput(12, r"^JC-(\d{6})\.zip$", store)


def test_ingest_result_cannot_be_empty():
    with pytest.raises(ValueError):
        IngestResult(files=(), period_start=None, period_end=None)

def test_process_input_coverage_settings(weather_frame):
    """Require source metadata, validate the buffer and preserve raw trips."""
    trips = read_raw_trips_csv(io.StringIO(CSV))
    original = trips.copy(deep=True)
    months = (date(2026, 9, 1),)

    result = ProcessInput(
        trips=trips,
        loaded_months=months,
        weather=weather_frame,
    )

    assert result.trips is trips
    assert result.loaded_months == months
    assert result.departure_buffer_hours == 48

    for invalid_months in (
        (),
        [date(2026, 9, 1)],
        (date(2026, 9, 2),),
        ("2026-09",),
    ):
        with pytest.raises(ValueError, match="loaded_months"):
            ProcessInput(
                trips=trips,
                loaded_months=invalid_months,
                weather=weather_frame,
            )

    for buffer in (0, -1, 48.5, True):
        with pytest.raises(ValueError, match="departure_buffer_hours"):
            ProcessInput(
                trips=trips,
                loaded_months=months,
                weather=weather_frame,
                departure_buffer_hours=buffer,
            )

    pd.testing.assert_frame_equal(trips, original)


@pytest.fixture
def process_result_kwargs():
    """A minimal valid processed table and its metadata."""
    return {
        "dataset": pd.DataFrame(
            {
                "station_id": ["JC001"],
                "timestamp": pd.to_datetime(
                    ["2026-01-05 13:00"], utc=True,
                ),
                "hour": [8],
                "departures": [2],
                "arrivals": [1],
            },
        ),
        "feature_columns": ("station_id", "hour"),
        "dropped_rows": {"duplicate_rows": 1},
        "loaded_months": (date(2026, 1, 1),),
        "departure_buffer_hours": 72,
    }


def test_process_result_metadata(process_result_kwargs):
    """Record targets, the actual buffer and both modeling assumptions."""
    original = process_result_kwargs["dataset"].copy(deep=True)

    result = ProcessResult(**process_result_kwargs)

    assert result.dataset is process_result_kwargs["dataset"]
    assert result.feature_columns == ("station_id", "hour")
    assert result.target_columns == ("departures", "arrivals")
    assert result.dropped_rows == {"duplicate_rows": 1}
    assert result.loaded_months == (date(2026, 1, 1),)
    assert result.departure_buffer_hours == 72
    assert result.station_activity_assumption == "all_input_stations_active"
    assert result.coverage_policy == "departure_buffer_approximation"
    assert result.holiday_calendar == "US_federal_actual_and_observed"
    assert result.weather_excluded_station_hours == 0

    pd.testing.assert_frame_equal(result.dataset, original)


def test_process_result_rejects_invalid_manifest(process_result_kwargs):
    """Reject leaking targets, duplicate names and absent feature columns."""
    for features in (
        (),
        ("station_id", "hour", "hour"),
        ("station_id", "departures"),
        ("station_id", "timestamp"),
        ("station_id", "unknown_feature"),
    ):
        with pytest.raises(
            ValueError,
            match="feature_columns|missing columns",
        ):
            ProcessResult(
                **(process_result_kwargs | {"feature_columns": features})
            )
        with pytest.raises(ValueError, match="weather_excluded_station_hours"):
            ProcessResult(
                **(
                    process_result_kwargs
                    | {"weather_excluded_station_hours": -1}
                )
            )


def test_process_result_rejects_invalid_data(process_result_kwargs):
    """Reject unusable hourly keys, target values and removal counts."""
    frame = process_result_kwargs["dataset"]

    duplicate = pd.concat([frame, frame], ignore_index=True)
    naive = frame.assign(
        timestamp=frame["timestamp"].dt.tz_localize(None),
    )
    partial = frame.assign(
        timestamp=frame["timestamp"] + pd.Timedelta(minutes=1),
    )
    negative = frame.assign(departures=-1)
    fractional = frame.assign(arrivals=0.5)
    missing = frame.assign(hour=float("nan"))

    for invalid, message in (
        (duplicate, "duplicate station-hour"),
        (naive, "UTC"),
        (partial, "hour starts"),
        (negative, "non-negative integers"),
        (fractional, "non-negative integers"),
        (missing, "missing values"),
    ):
        with pytest.raises(ValueError, match=message):
            ProcessResult(
                **(process_result_kwargs | {"dataset": invalid})
            )

    with pytest.raises(ValueError, match="dropped_rows"):
        ProcessResult(
            **(
                process_result_kwargs
                | {"dropped_rows": {"duplicate_rows": -1}}
            )
        )

def test_process_input_accepts_missing_weather_values(weather_frame):
    """Keep missing observations for explicit exclusion during the join."""
    trips = read_raw_trips_csv(io.StringIO(CSV))
    weather_frame.loc[0, "temperature_c"] = float("nan")

    for weather in (weather_frame, weather_frame.iloc[:0]):
        original = weather.copy(deep=True)

        result = ProcessInput(
            trips,
            (date(2026, 9, 1),),
            weather=weather,
        )

        assert result.weather is weather
        pd.testing.assert_frame_equal(weather, original)


def test_process_input_rejects_invalid_weather(weather_frame):
    """Reject malformed times, duplicates and invalid numeric observations."""
    trips = read_raw_trips_csv(io.StringIO(CSV))

    naive = weather_frame.assign(
        timestamp=weather_frame["timestamp"].dt.tz_localize(None),
    )
    partial = weather_frame.assign(
        timestamp=weather_frame["timestamp"] + pd.Timedelta(minutes=1),
    )
    duplicate = pd.concat(
        [weather_frame, weather_frame], ignore_index=True,
    )

    for weather, message in (
        (None, "DataFrame"),
        (weather_frame.drop(columns="wind_speed_ms"), "columns"),
        (weather_frame.assign(extra=1), "columns"),
        (naive, "UTC"),
        (partial, "hour starts"),
        (duplicate, "duplicate timestamps"),
        (weather_frame.assign(temperature_c="warm"), "numeric"),
        (weather_frame.assign(wind_speed_ms=True), "numeric"),
        (weather_frame.assign(temperature_c=float("inf")), "finite"),
        (weather_frame.assign(precipitation_mm=-0.1), "non-negative"),
        (weather_frame.assign(wind_speed_ms=-1.0), "non-negative"),
    ):
        with pytest.raises(ValueError, match=message):
            ProcessInput(
                trips,
                (date(2026, 9, 1),),
                weather=weather,
            )