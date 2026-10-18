"""PipelineRunService, repository and ORM models against a real PostgreSQL."""

import time
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from bikeflow.service.dto import IngestedFileDto, PipelineRunDto
from bikeflow.service.pipeline_run_service import PipelineRunService, RunNotFoundError
from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger


@pytest.fixture
def service(session_factory):
    return PipelineRunService(session_factory)


def ingest_file(month: int) -> IngestedFileDto:
    return IngestedFileDto(
        key=f"runs/x/steps/ingest/JC-2026{month:02d}.zip",
        month=date(2026, month, 1),
        source_url="https://example.test/file.zip",
        etag="abc",
        size=100,
        downloaded_at=datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc),
    )


def test_create_fills_database_defaults_and_all_steps(service):
    run = service.create(Trigger.SCHEDULER, window_months=12)

    assert run.id is not None
    assert run.status == RunStatus.CREATED  # DEFAULT from the migration
    assert run.trigger == Trigger.SCHEDULER and run.window_months == 12
    assert run.created_at.tzinfo is not None and run.updated_at is not None
    assert [s.step_type for s in run.steps] == list(StepType)
    assert all(s.status == StepStatus.PENDING and s.id is not None for s in run.steps)


def test_find_by_id(service):
    created = service.create(Trigger.MANUAL, window_months=6)

    assert service.find_by_id(created.id) == created
    assert service.find_by_id(created.id + 1000) is None


def test_update_saves_step_results(service):
    run = service.create(Trigger.MANUAL, window_months=12)
    ingest = run.steps[0].model_copy(update={
        "status": StepStatus.COMPLETED,
        "period_start": date(2026, 8, 1),
        "period_end": date(2026, 9, 30),
        "files": (ingest_file(8), ingest_file(9)),
    })

    service.update(run.model_copy(update={"status": RunStatus.RUNNING, "steps": [ingest, *run.steps[1:]]}))

    saved = service.find_by_id(run.id)
    assert saved.status == RunStatus.RUNNING
    assert saved.steps[0] == ingest.model_copy(update={"id": run.steps[0].id})
    assert [s.status for s in saved.steps[1:]] == [StepStatus.PENDING] * 4


def test_update_of_a_step_only_refreshes_run_updated_at(service):
    run = service.create(Trigger.MANUAL, window_months=12)
    time.sleep(0.01)
    step = run.steps[1].model_copy(update={"status": StepStatus.RUNNING})

    updated = service.update(run.model_copy(update={"steps": [run.steps[0], step, *run.steps[2:]]}))

    assert updated.updated_at > run.updated_at


def test_update_without_id_is_rejected(service):
    with pytest.raises(ValueError):
        service.update(PipelineRunDto(trigger=Trigger.MANUAL, window_months=12))


def test_update_of_missing_run_is_rejected(service):
    run = service.create(Trigger.MANUAL, window_months=12)
    with pytest.raises(RunNotFoundError):
        service.update(run.model_copy(update={"id": run.id + 1000}))


def test_delete_removes_run_with_steps(service, engine):
    run = service.create(Trigger.MANUAL, window_months=12)

    assert service.delete(run.id) is True
    assert service.find_by_id(run.id) is None
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pipeline_step")).scalar() == 0
    assert service.delete(run.id) is False
