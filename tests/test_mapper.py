import json
from datetime import date, datetime, timezone

from bikeflow.service.dto import IngestedFileDto, IngestStepDto, PipelineRunDto
from bikeflow.service.mapper import _STEP_DTO_BY_TYPE, apply_run, run_to_dto
from bikeflow.storage.models import PipelineRun, StepStatus, StepType, Trigger


def test_every_step_type_has_a_dto():
    assert set(_STEP_DTO_BY_TYPE) == set(StepType)


def test_ingest_step_survives_a_database_round_trip():
    ingest = IngestStepDto(
        status=StepStatus.COMPLETED,
        period_start=date(2025, 10, 1),
        period_end=date(2026, 9, 30),
        files=(
            IngestedFileDto(
                key="runs/2026-10-18_15-30-00Z_run-42/steps/ingest/JC-202609-citibike-tripdata.csv.zip",
                month=date(2026, 9, 1),
                source_url="https://s3.amazonaws.com/tripdata/JC-202609-citibike-tripdata.csv.zip",
                etag="742bcd8d239247fdaab1b8a8235291b8",
                size=3713499,
                downloaded_at=datetime(2026, 10, 18, 15, 31, tzinfo=timezone.utc),
            ),
        ),
    )
    run = PipelineRun(steps=[])
    apply_run(PipelineRunDto(trigger=Trigger.MANUAL, window_months=12, steps=[ingest]), run)

    # JSONB stores plain JSON: what comes back from the database is parsed JSON.
    stored = run.steps[0].step_data
    run.steps[0].step_data = json.loads(json.dumps(stored))

    assert run_to_dto(run).steps[0] == ingest
    assert set(stored) == {"period_start", "period_end", "files"}  # common fields are columns, not JSON
