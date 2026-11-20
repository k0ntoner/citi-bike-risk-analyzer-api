"""PROCESS handler of the orchestrator: months and weather in, Parquet dataset out."""

from datetime import date

import pandas as pd
import pytest

from bikeflow.contracts.process import WEATHER_FEATURE_COLUMNS
from bikeflow.service.dto import ProcessStepDto
from bikeflow.steps.process import ProcessService
from bikeflow.storage.models import StepStatus, StepType
from tests.test_pipeline_analyze_raw import run_pipeline
from tests.test_pipeline_ingest import RUN_FOLDER


class FakeWeather:
    """Constant weather for every requested hour; records the requested period."""

    latitude = 40.73
    longitude = -74.04

    def __init__(self):
        self.requests = []

    def fetch(self, start, end):
        self.requests.append((start, end))
        hours = pd.date_range(start, end, freq="h", inclusive="left")
        return pd.DataFrame({
            "timestamp": hours,
            "temperature_c": 18.0,
            "precipitation_mm": 0.0,
            "wind_speed_ms": 3.0,
        })


@pytest.fixture
def weather():
    return FakeWeather()


def run(tmp_path, weather):
    pipeline, finished, runs = run_pipeline(tmp_path, ProcessService(), weather)
    step = next(s for s in finished.steps if s.step_type == StepType.PROCESS)
    return pipeline, step, runs


def test_builds_the_dataset_of_our_stations_only(tmp_path, weather):
    _, step, _ = run(tmp_path, weather)

    assert step.status == StepStatus.COMPLETED
    assert step.dataset_key == f"{RUN_FOLDER}/steps/process/dataset.parquet"
    dataset = pd.read_parquet(tmp_path / step.dataset_key)
    # Our September trips start at JC102 and JC072; the October inflow trips
    # end at JC076 and HB201. The New York ends (5114.08, 6578.01, ...) are not rows.
    assert set(dataset["station_id"]) == {"HB201", "JC072", "JC076", "JC102"}
    assert dataset["departures"].sum() == 2
    assert (step.rows, step.stations) == (len(dataset), 4)
    assert step.target_columns == ("departures", "arrivals")
    assert set(WEATHER_FEATURE_COLUMNS) <= set(step.feature_columns)


def test_dataset_keeps_its_types_in_parquet(tmp_path, weather):
    _, step, _ = run(tmp_path, weather)
    dataset = pd.read_parquet(tmp_path / step.dataset_key)

    assert str(dataset["timestamp"].dt.tz) == "UTC"
    assert pd.api.types.is_integer_dtype(dataset["departures"])
    assert dataset["is_weekend"].dtype == bool


def test_weather_is_fetched_for_the_loaded_months_in_utc(tmp_path, weather):
    _, step, _ = run(tmp_path, weather)

    # Two INGEST files (ours and the inflow) of one month: the month counts once.
    assert step.loaded_months == (date(2026, 9, 1),)
    # Local midnight of September 1 and October 1 (EDT, UTC-4).
    assert weather.requests == [(pd.Timestamp("2026-09-01 04:00", tz="UTC"), pd.Timestamp("2026-10-01 04:00", tz="UTC"))]
    assert (step.weather_latitude, step.weather_longitude) == (40.73, -74.04)


def test_window_end_is_cut_by_the_departure_buffer(tmp_path, weather):
    _, step, _ = run(tmp_path, weather)

    assert step.departure_buffer_hours == 48
    assert step.period_start == pd.Timestamp("2026-09-01 04:00", tz="UTC")
    assert step.period_end == pd.Timestamp("2026-09-29 03:00", tz="UTC")  # last hour before the 48 h buffer


def test_summary_survives_the_json_round_trip_of_step_data(tmp_path, weather):
    _, _, runs = run(tmp_path, weather)
    saved = next(s for s in runs.saved.steps if s.step_type == StepType.PROCESS)

    assert ProcessStepDto.model_validate_json(saved.model_dump_json()) == saved
    assert saved.station_activity_assumption == "all_input_stations_active"


def test_raw_trips_are_released_after_process(tmp_path, weather):
    pipeline, _, _ = run(tmp_path, weather)
    assert pipeline._raw_trips == {}


def test_process_without_weather_source_fails(tmp_path):
    _, finished, _ = run_pipeline(tmp_path, ProcessService(), None)
    step = next(s for s in finished.steps if s.step_type == StepType.PROCESS)

    assert step.status == StepStatus.FAILED
    assert "weather source" in step.error_message
