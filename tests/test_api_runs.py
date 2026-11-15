"""Run endpoints with the services replaced by fakes (like @MockBean in Spring)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from bikeflow.api.app import create_app
from bikeflow.api.runs import get_pipeline, get_run_service
from bikeflow.service.dto import PipelineRunDto, StationActivityDto
from bikeflow.service.mapper import _STEP_DTO_BY_TYPE
from bikeflow.storage.models import RunStatus, StepType, Trigger


def make_run(run_id: int, trigger=Trigger.MANUAL) -> PipelineRunDto:
    return PipelineRunDto(
        id=run_id, trigger=trigger, window_months=12,
        created_at=datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc),
        steps=[_STEP_DTO_BY_TYPE[t]() for t in StepType],
    )


class FakePipeline:
    def __init__(self):
        self.executed = []

    def create_run(self, trigger):
        return make_run(7, trigger)

    def execute(self, run):
        self.executed.append(run.id)

    def station_activity(self, run):
        return self.stations


class FakeRunService:
    def find_by_id(self, run_id):
        return make_run(run_id) if run_id == 7 else None


@pytest.fixture
def pipeline():
    return FakePipeline()


@pytest.fixture
def client(pipeline):
    app = create_app(migrate=False)
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    app.dependency_overrides[get_run_service] = FakeRunService
    with TestClient(app) as client:
        yield client


def test_start_run_returns_created_run_and_executes_it_in_background(client, pipeline):
    response = client.post("/runs")

    assert response.status_code == 202
    assert response.headers["Location"] == "/runs/7"
    body = response.json()
    assert (body["id"], body["trigger"], body["status"]) == (7, "MANUAL", RunStatus.CREATED)
    assert pipeline.executed == [7]  # TestClient runs background tasks before returning


def test_get_run_returns_full_run_with_steps_in_order(client):
    response = client.get("/runs/7")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 7
    assert [s["step_type"] for s in body["steps"]] == [t.value for t in StepType]
    assert body["steps"][0]["files"] == []  # step-specific fields are included


def test_get_missing_run_is_404(client):
    response = client.get("/runs/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Pipeline run 999 not found"}


def test_get_station_activity(client, pipeline):
    pipeline.stations = [StationActivityDto(
        station_id="JC115", station_name="Hamilton Park", departures=40, arrivals=38, in_network=True,
    )]
    response = client.get("/runs/7/analyze-raw/stations")

    assert response.status_code == 200
    assert response.json() == [{
        "station_id": "JC115", "station_name": "Hamilton Park", "departures": 40, "arrivals": 38, "in_network": True,
    }]


def test_station_activity_before_analyze_raw_is_404(client, pipeline):
    pipeline.stations = None
    assert client.get("/runs/7/analyze-raw/stations").status_code == 404
    assert client.get("/runs/999/analyze-raw/stations").status_code == 404
