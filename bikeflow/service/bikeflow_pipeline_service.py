"""Pipeline orchestrator."""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from bikeflow import config
from bikeflow.contracts.ingest import IngestInput
from bikeflow.service.dto import IngestedFileDto, PipelineRunDto
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.steps.analyze_features import AnalyzeFeaturesService
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest_service import IngestService
from bikeflow.steps.process import ProcessService
from bikeflow.steps.train import TrainService
from bikeflow.storage.dataset_store import LocalDatasetStore
from bikeflow.storage.layout import run_dir, step_dir
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
        data_root: str | Path = config.DATA_ROOT,
    ) -> None:
        self._pipeline_service = pipeline_service
        self._data_root = Path(data_root)
        self._ingest_service = ingest_service
        self._analyze_raw_service = analyze_raw_service
        self._process_service = process_service
        self._analyze_features_service = analyze_features_service
        self._train_service = train_service
        # Handler of each step type; the execution order comes from StepType.order.
        # Every handler takes the current run and returns it with its step's
        # result filled in. It builds the step input (from the configuration and
        # earlier steps), calls the step service and maps the result back.
        self._handlers: dict[StepType, Callable[[PipelineRunDto], PipelineRunDto]] = {
            StepType.INGEST: self._handle_ingest_step,
            StepType.ANALYZE_RAW: self._handle_not_wired_step,
            StepType.PROCESS: self._handle_not_wired_step,
            StepType.ANALYZE_FEATURES: self._handle_not_wired_step,
            StepType.TRAIN: self._handle_not_wired_step,
        }

    def run(self, trigger: Trigger) -> PipelineRunDto:
        """Create a run and execute it right away; returns the finished run."""
        return self.execute(self.create_run(trigger))

    def create_run(self, trigger: Trigger) -> PipelineRunDto:
        """Create a run with all steps PENDING, without executing it.

        Lets a caller return the run id at once and execute it later, e.g. in
        the background of an HTTP request.
        """
        run = self._pipeline_service.create(
                trigger=trigger,
                window_months=config.TRIPDATA_WINDOW_MONTHS,
        )
        log.info("Run %s created by %s", run.id, trigger)
        return run

    def execute(self, run: PipelineRunDto) -> PipelineRunDto:
        """Execute the steps of a created run and return the finished run.

        A failing step does not raise: the error is stored in the step and the
        returned run has status FAILED.
        """
        run = self._update(run, status=RunStatus.RUNNING, started_at=_now())

        # Do not rely on the order steps come back from the database.
        for step in sorted(run.steps, key=lambda s: s.order):
            step_type = step.step_type
            handler = self._handlers[step_type]
            run = self._save_step(run, step_type, status=StepStatus.RUNNING, started_at=_now())
            try:
                run = handler(run)
            except Exception as e:
                log.exception("Run %s: step %s failed", run.id, step_type)
                run = self._save_step(
                    run, step_type,
                    status=StepStatus.FAILED, completed_at=_now(), error_message=str(e) or type(e).__name__,
                )
                return self._fail(run)
            run = self._save_step(run, step_type, status=StepStatus.COMPLETED, completed_at=_now())

        log.info("Run %s completed", run.id)
        return self._update(run, status=RunStatus.COMPLETED, completed_at=_now())

    def _fail(self, run: PipelineRunDto) -> PipelineRunDto:
        """Mark the remaining PENDING steps SKIPPED and the run FAILED."""
        steps = [
            step.model_copy(update={"status": StepStatus.SKIPPED})
            if step.status == StepStatus.PENDING else step
            for step in run.steps
        ]
        return self._update(run, status=RunStatus.FAILED, completed_at=_now(), steps=steps)

    def _save_step(self, run: PipelineRunDto, step_type: StepType, **changes) -> PipelineRunDto:
        """Apply changes to one step of the run and save the run."""
        return self._update(self._with_step(run, step_type, **changes))

    @staticmethod
    def _with_step(run: PipelineRunDto, step_type: StepType, **changes) -> PipelineRunDto:
        """Copy of the run with changes applied to one step; nothing is saved."""
        steps = [
            step.model_copy(update=changes) if step.step_type == step_type else step
            for step in run.steps
        ]
        return run.model_copy(update={"steps": steps})

    def _update(self, run: PipelineRunDto, **changes) -> PipelineRunDto:
        """Apply changes to the run and save it."""
        return self._pipeline_service.update(run.model_copy(update=changes))

    def _handle_ingest_step(self, run: PipelineRunDto) -> PipelineRunDto:
        """INGEST: download the trip files of the window into the run folder.

        The files go to ``{data_root}/runs/<run>/steps/ingest``; their keys are
        stored relative to the data root, so later steps can read them.
        """
        folder = step_dir(run_dir(run.id, run.created_at), StepType.INGEST)
        result = self._ingest_service.ingest(IngestInput(
            window_months=run.window_months,
            file_regex=config.TRIPDATA_FILE_REGEX,
            target=LocalDatasetStore(self._data_root / folder),
        ))
        files = tuple(
            IngestedFileDto(
                key=f"{folder}/{file.key}",
                month=file.month,
                source_url=file.source_url,
                etag=file.etag,
                size=file.size,
                downloaded_at=file.downloaded_at,
            )
            for file in result.files
        )
        log.info("Run %s: ingested %d files, %s .. %s", run.id, len(files), result.period_start, result.period_end)
        return self._with_step(
            run, StepType.INGEST,
            period_start=result.period_start, period_end=result.period_end, files=files,
        )

    def _handle_not_wired_step(self, run: PipelineRunDto) -> PipelineRunDto:
        """Placeholder for steps whose input from earlier steps is not wired yet."""
        raise NotImplementedError("This step is not wired into the pipeline yet")

