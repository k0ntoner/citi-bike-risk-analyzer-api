"""ANALYZE_RAW handler of the orchestrator: reads the INGEST files, stores the result."""

import io
import zipfile
from datetime import datetime, timezone

from bikeflow import config
from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.dto import AnalyzeRawStepDto, PipelineRunDto
from bikeflow.service.mapper import _STEP_DTO_BY_TYPE
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest_service import IngestService
from bikeflow.storage.models import StepStatus, StepType, Trigger
from tests.test_ingest_inflow import source
from tests.test_ingest_service import jc
from tests.test_pipeline_ingest import RUN_FOLDER, FakeRuns
from tests.test_raw_trips import CSV


def jc_zip():
    """Our month with two trips: JC102 -> New York and JC072 -> no end station."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("JC-202609-citibike-tripdata.csv", CSV)
    return buffer.getvalue()


def run_pipeline(tmp_path):
    fake = source(["202609"], ["202609"])
    fake.contents[jc("202609")] = jc_zip()
    runs = FakeRuns()
    pipeline = BikeFlowPipelineService(
        runs, IngestService(fake), AnalyzeRawService(), None, None, None,
        data_root=tmp_path, inflow_file_regex=config.INFLOW_FILE_REGEX,
    )
    return pipeline, pipeline.run(Trigger.MANUAL), runs


def analyze_raw(run):
    return next(s for s in run.steps if s.step_type == StepType.ANALYZE_RAW)


def test_analyzes_our_trips_together_with_the_inflow(tmp_path):
    _, run, _ = run_pipeline(tmp_path)
    step = analyze_raw(run)

    assert step.status == StepStatus.COMPLETED
    # 2 trips of our file + 2 trips from New York into the network.
    assert step.general_statistics.total_trips == 4
    assert step.data_quality.missing_values["end_station_id"] == 1
    assert step.distributions.by_month == {9: 2, 10: 2}
    assert step.stations_key == f"{RUN_FOLDER}/steps/analyze_raw/stations.csv"
    assert (tmp_path / step.stations_key).is_file()


def test_period_is_stored_as_utc(tmp_path):
    step = analyze_raw(run_pipeline(tmp_path)[1])

    # The first trip starts at 08:11:36.020 New York time (EDT, UTC-4).
    assert step.general_statistics.period_start == datetime(2026, 9, 8, 12, 11, 36, 20000, tzinfo=timezone.utc)


def test_result_survives_the_json_round_trip_of_step_data(tmp_path):
    _, _, runs = run_pipeline(tmp_path)
    saved = analyze_raw(runs.saved)

    restored = AnalyzeRawStepDto.model_validate_json(saved.model_dump_json())
    assert restored == saved  # e.g. hour keys come back as ints, times as aware datetimes


def test_station_table_marks_network_stations(tmp_path):
    pipeline, run, _ = run_pipeline(tmp_path)

    stations = {s.station_id: s for s in pipeline.station_activity(run)}
    assert stations["JC102"].in_network and stations["JC102"].departures == 1
    assert not stations["5114.08"].in_network  # a New York station our trip ended at
    assert stations["6578.01"].departures == 1  # a New York station a trip to us started at
    assert stations["JC076"].arrivals == 1


def test_station_table_is_none_before_the_step_completes():
    pipeline = BikeFlowPipelineService(None, None, None, None, None, None)
    run = PipelineRunDto(id=1, trigger=Trigger.MANUAL, window_months=1, steps=[_STEP_DTO_BY_TYPE[t]() for t in StepType])
    assert pipeline.station_activity(run) is None


def test_trips_of_a_run_are_released_when_it_ends(tmp_path):
    pipeline, _, _ = run_pipeline(tmp_path)
    assert pipeline._raw_trips == {}
