"""Pipeline orchestrator."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from bikeflow import config
from bikeflow.service.dto import (
    AnalyzeFeaturesStepDto,
    AnalyzeRawStepDto,
    IngestStepDto,
    PipelineRunDto,
    ProcessStepDto,
    TrainStepDto,
)
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.steps.analyze_features import AnalyzeFeaturesService
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest import IngestService
from bikeflow.steps.process import ProcessService
from bikeflow.steps.train import TrainService
from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BikeFlowPipelineService:
    """Runs the whole pipeline.

    Steps: ingest -> analyze raw -> process -> analyze features -> train.

    Started by a user or by the scheduler. Creates a run with all steps PENDING,
    executes the steps in order and saves every status change right away, so the
    history shows the progress of a run while it executes. If a step fails, the
    remaining steps are SKIPPED and the run is FAILED.

    This is the only place that reads the configuration and talks to storage;
    the step services get their input through method arguments.
    """

    def __init__(
        self,
        pipeline_service: PipelineRunService,
        ingest_service: IngestService,
        analyze_raw_service: AnalyzeRawService,
        process_service: ProcessService,
        analyze_features_service: AnalyzeFeaturesService,
        train_service: TrainService,
    ) -> None:
        self._pipeline_service = pipeline_service
        # Step actions in execution order. Passing data between steps is added
        # here once the step contracts are defined.
        self._actions: dict[StepType, Callable[[], None]] = {
            StepType.INGEST: ingest_service.ingest,
            StepType.ANALYZE_RAW: analyze_raw_service.analyze_raw,
            StepType.PROCESS: process_service.process,
            StepType.ANALYZE_FEATURES: analyze_features_service.analyze_features,
            StepType.TRAIN: train_service.train,
        }

    def run(self, trigger: Trigger) -> PipelineRunDto:
        """Execute the pipeline once and return the finished run.

        A failing step does not raise: the error is stored in the step and the
        returned run has status FAILED.
        """
        run = self._pipeline_service.save(
            PipelineRunDto(
                trigger=trigger,
                window_months=config.TRIPDATA_WINDOW_MONTHS,
                steps=[
                    IngestStepDto(),
                    AnalyzeRawStepDto(),
                    ProcessStepDto(),
                    AnalyzeFeaturesStepDto(),
                    TrainStepDto(),
                ],
            )
        )
        log.info("Run %s created by %s", run.id, trigger)
        run = self._save(run, status=RunStatus.RUNNING, started_at=_now())

        for step_type, action in self._actions.items():
            run = self._save_step(run, step_type, status=StepStatus.RUNNING, started_at=_now())
            try:
                action()
            except Exception as e:
                log.exception("Run %s: step %s failed", run.id, step_type)
                run = self._save_step(
                    run, step_type,
                    status=StepStatus.FAILED, completed_at=_now(), error_message=str(e) or type(e).__name__,
                )
                return self._fail(run)
            run = self._save_step(run, step_type, status=StepStatus.COMPLETED, completed_at=_now())

        log.info("Run %s completed", run.id)
        return self._save(run, status=RunStatus.COMPLETED, completed_at=_now())

    def _fail(self, run: PipelineRunDto) -> PipelineRunDto:
        """Mark the remaining PENDING steps SKIPPED and the run FAILED."""
        steps = [
            step.model_copy(update={"status": StepStatus.SKIPPED})
            if step.status == StepStatus.PENDING else step
            for step in run.steps
        ]
        return self._save(run, status=RunStatus.FAILED, completed_at=_now(), steps=steps)

    def _save_step(self, run: PipelineRunDto, step_type: StepType, **changes) -> PipelineRunDto:
        """Apply changes to one step of the run and save the run."""
        steps = [
            step.model_copy(update=changes) if step.step_type == step_type else step
            for step in run.steps
        ]
        return self._save(run, steps=steps)

    def _save(self, run: PipelineRunDto, **changes) -> PipelineRunDto:
        return self._pipeline_service.save(run.model_copy(update=changes))
