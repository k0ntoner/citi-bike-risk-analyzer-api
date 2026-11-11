"""INGEST handler of the orchestrator, with fake run storage and a fake source."""

from datetime import date, datetime, timezone

from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.dto import PipelineRunDto
from bikeflow.service.mapper import _STEP_DTO_BY_TYPE
from bikeflow.steps.ingest_service import IngestService
from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger
from bikeflow import config
from tests.test_ingest_inflow import source
from tests.test_ingest_service import FakeSource, jc

CREATED_AT = datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc)
RUN_FOLDER = "runs/2026-10-18_15-30-00Z_run-42"


class FakeRuns:
    """Run service that keeps the last saved state in memory."""

    def create(self, trigger, window_months):
        steps = [_STEP_DTO_BY_TYPE[t]() for t in StepType]
        return PipelineRunDto(id=42, trigger=trigger, window_months=window_months, created_at=CREATED_AT, steps=steps)

    def update(self, dto):
        self.saved = dto
        return dto


def run_pipeline(tmp_path, source, inflow_file_regex=None):
    runs = FakeRuns()
    pipeline = BikeFlowPipelineService(
        runs, IngestService(source), None, None, None, None,
        data_root=tmp_path, inflow_file_regex=inflow_file_regex,
    )
    return pipeline.run(Trigger.MANUAL), runs


def test_ingest_stores_files_in_run_folder_and_records_them(tmp_path):
    run, runs = run_pipeline(tmp_path, FakeSource([jc("202608"), jc("202609")]))

    ingest = next(s for s in run.steps if s.step_type == StepType.INGEST)
    assert ingest.status == StepStatus.COMPLETED
    assert (ingest.period_start, ingest.period_end) == (date(2026, 8, 1), date(2026, 9, 30))
    assert [f.key for f in ingest.files] == [
        f"{RUN_FOLDER}/steps/ingest/{jc('202608')}",
        f"{RUN_FOLDER}/steps/ingest/{jc('202609')}",
    ]
    for file in ingest.files:
        assert (tmp_path / file.key).is_file()  # keys are relative to the data root
    assert runs.saved.steps[0].files == ingest.files  # the result was saved, not only returned


def test_ingest_records_inflow_files(tmp_path):
    run, _ = run_pipeline(tmp_path, source(["202609"], ["202609"]), inflow_file_regex=config.INFLOW_FILE_REGEX)

    ingest = next(s for s in run.steps if s.step_type == StepType.INGEST)
    assert [f.key for f in ingest.files] == [
        f"{RUN_FOLDER}/steps/ingest/{jc('202609')}",
        f"{RUN_FOLDER}/steps/ingest/202609-citibike-tripdata-inflow.csv",
    ]
    assert [(f.scanned_trips, f.kept_trips) for f in ingest.files] == [(None, None), (4, 2)]
    assert (tmp_path / ingest.files[1].key).is_file()


def test_second_run_takes_files_from_the_shared_cache(tmp_path):
    fake = source(["202609"], ["202609"])
    first, _ = run_pipeline(tmp_path, fake, inflow_file_regex=config.INFLOW_FILE_REGEX)
    second, _ = run_pipeline(tmp_path, fake, inflow_file_regex=config.INFLOW_FILE_REGEX)

    assert [f.cached for f in first.steps[0].files] == [False, False]
    assert [f.cached for f in second.steps[0].files] == [True, True]
    assert (tmp_path / "cache" / "ingest").is_dir()


def test_failed_ingest_fails_the_run(tmp_path):
    run, _ = run_pipeline(tmp_path, FakeSource(["unrelated.zip"]))

    statuses = {s.step_type: s.status for s in run.steps}
    assert run.status == RunStatus.FAILED
    assert statuses[StepType.INGEST] == StepStatus.FAILED
    assert all(statuses[t] == StepStatus.SKIPPED for t in StepType if t != StepType.INGEST)
    assert "No published file matches" in run.steps[0].error_message
