"""Pipeline run endpoints."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status

from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.dto import PipelineRunDto, StationActivityDto
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.storage.models import Trigger

router = APIRouter(prefix="/runs", tags=["runs"])


def get_pipeline(request: Request) -> BikeFlowPipelineService:
    """The orchestrator built in ``create_app``; overridden in tests."""
    return request.app.state.pipeline


def get_run_service(request: Request) -> PipelineRunService:
    """The run service built in ``create_app``; overridden in tests."""
    return request.app.state.run_service


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=PipelineRunDto)
def start_run(
    response: Response,
    background_tasks: BackgroundTasks,
    pipeline: Annotated[BikeFlowPipelineService, Depends(get_pipeline)],
) -> PipelineRunDto:
    """Start the pipeline manually.

    Creates the run and returns it at once with status CREATED; the steps are
    executed in the background after the response is sent. Follow the progress
    with ``GET /runs/{id}`` (URL in the ``Location`` header).
    """
    run = pipeline.create_run(Trigger.MANUAL)
    background_tasks.add_task(pipeline.execute, run)
    response.headers["Location"] = f"{router.prefix}/{run.id}"
    return run


@router.get("/{run_id}", response_model=PipelineRunDto)
def get_run(
    run_id: int,
    run_service: Annotated[PipelineRunService, Depends(get_run_service)],
) -> PipelineRunDto:
    """A run with all its steps and their results.

    Raises:
        HTTPException: 404 if the run does not exist.
    """
    return _find_run(run_id, run_service)


@router.get("/{run_id}/analyze-raw/stations", response_model=list[StationActivityDto])
def get_station_activity(
    run_id: int,
    run_service: Annotated[PipelineRunService, Depends(get_run_service)],
    pipeline: Annotated[BikeFlowPipelineService, Depends(get_pipeline)],
) -> list[StationActivityDto]:
    """Departures and arrivals of every station found in the raw trips of a run.

    ``in_network`` separates our stations (Jersey City / Hoboken) from New York
    stations that appear only because a trip crossed the river.

    Raises:
        HTTPException: 404 if the run does not exist or its ANALYZE_RAW step
            has not completed.
    """
    stations = pipeline.station_activity(_find_run(run_id, run_service))
    if stations is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"ANALYZE_RAW of run {run_id} has not completed")
    return stations


def _find_run(run_id: int, run_service: PipelineRunService) -> PipelineRunDto:
    """The run with this id.

    Raises:
        HTTPException: 404 if the run does not exist.
    """
    run = run_service.find_by_id(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline run {run_id} not found")
    return run
