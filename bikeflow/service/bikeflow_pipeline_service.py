"""Pipeline orchestrator."""

import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from bikeflow import config
from bikeflow.contracts.analyze_raw import AnalyzeRawInput
from bikeflow.contracts.ingest import IngestInput
from bikeflow.contracts.raw_trips import read_raw_trips_csv
from bikeflow.service.dto import (
    AnalyzeRawStepDto,
    IngestedFileDto,
    IngestStepDto,
    PipelineRunDto,
    StationActivityDto,
)
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.steps.analyze_features import AnalyzeFeaturesService
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest_service import IngestService
from bikeflow.steps.process import ProcessService
from bikeflow.steps.train import TrainService
from bikeflow.storage.dataset_store import LocalDatasetStore
from bikeflow.storage.layout import cache_dir, run_dir, step_dir
from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger

log = logging.getLogger(__name__)

# Trip timestamps are New York local time without a zone.
_TRIP_TIMEZONE = ZoneInfo("America/New_York")
_STATIONS_FILE = "stations.csv"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _trip_time_to_utc(value: datetime | None) -> datetime | None:
    """Trip time (New York local, no zone) as timezone-aware UTC, for storing in the DB.

    In the repeated hour of the autumn DST change the first occurrence is taken.
    """
    if value is None:
        return None
    return value.replace(tzinfo=_TRIP_TIMEZONE).astimezone(timezone.utc)


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
        inflow_file_regex: str | None = config.INFLOW_FILE_REGEX,
    ) -> None:
        self._pipeline_service = pipeline_service
        self._data_root = Path(data_root)
        self._inflow_file_regex = inflow_file_regex
        self._ingest_service = ingest_service
        self._analyze_raw_service = analyze_raw_service
        self._process_service = process_service
        self._analyze_features_service = analyze_features_service
        self._train_service = train_service
        # Raw trips of the runs being executed, by run id: read once from the
        # INGEST files and shared by ANALYZE_RAW and PROCESS. Dropped when the run ends.
        self._raw_trips: dict[int, pd.DataFrame] = {}
        # Handler of each step type; the execution order comes from StepType.order.
        # Every handler takes the current run and returns it with its step's
        # result filled in. It builds the step input (from the configuration and
        # earlier steps), calls the step service and maps the result back.
        self._handlers: dict[StepType, Callable[[PipelineRunDto], PipelineRunDto]] = {
            StepType.INGEST: self._handle_ingest_step,
            StepType.ANALYZE_RAW: self._handle_analyze_raw_step,
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
        try:
            return self._execute_steps(run)
        finally:
            self._raw_trips.pop(run.id, None)  # free the memory of the run's trips

    def _execute_steps(self, run: PipelineRunDto) -> PipelineRunDto:
        """Run the steps in order, saving every status change; see ``execute``."""
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

        Downloads are cached in ``{data_root}/cache/ingest`` by the ETag of the
        source file, so a repeated run copies them instead of downloading.
        The files go to ``{data_root}/runs/<run>/steps/ingest``; their keys are
        stored relative to the data root, so later steps can read them. With
        the inflow on, every month has two files: ours and the trips from New
        York into our network. Later steps read all of them as one dataset.
        """
        folder = step_dir(run_dir(run.id, run.created_at), StepType.INGEST)
        result = self._ingest_service.ingest(IngestInput(
            window_months=run.window_months,
            file_regex=config.TRIPDATA_FILE_REGEX,
            target=LocalDatasetStore(self._data_root / folder),
            inflow_file_regex=self._inflow_file_regex,
            network_station_prefixes=config.NETWORK_STATION_PREFIXES,
            cache=LocalDatasetStore(self._data_root / cache_dir(StepType.INGEST)),
        ))
        files = tuple(
            IngestedFileDto(
                key=f"{folder}/{file.key}",
                month=file.month,
                source_url=file.source_url,
                etag=file.etag,
                size=file.size,
                downloaded_at=file.downloaded_at,
                scanned_trips=file.scanned_trips,
                kept_trips=file.kept_trips,
                cached=file.cached,
            )
            for file in result.files
        )
        log.info(
            "Run %s: ingested %d files (%d from the cache), %s .. %s",
            run.id, len(files), sum(f.cached for f in files), result.period_start, result.period_end,
        )
        return self._with_step(
            run, StepType.INGEST,
            period_start=result.period_start, period_end=result.period_end, files=files,
        )

    def _handle_analyze_raw_step(self, run: PipelineRunDto) -> PipelineRunDto:
        """ANALYZE_RAW: describe the raw trips of the run for the UI.

        The summary groups go to ``step_data``; the per-station table is a
        table, so it goes to ``{data_root}/runs/<run>/steps/analyze_raw/stations.csv``
        and only its key is stored.
        """
        result = self._analyze_raw_service.analyze_raw(AnalyzeRawInput(self._load_raw_trips(run)))

        folder = step_dir(run_dir(run.id, run.created_at), StepType.ANALYZE_RAW)
        stations = pd.DataFrame(
            [dataclasses.asdict(s) for s in result.station_analysis.stations],
            columns=["station_id", "station_name", "departures", "arrivals"],
        )
        with LocalDatasetStore(self._data_root / folder).open_write(_STATIONS_FILE) as f:
            stations.to_csv(f, index=False)

        general = result.general_statistics
        general = dataclasses.replace(
            general,
            period_start=_trip_time_to_utc(general.period_start),
            period_end=_trip_time_to_utc(general.period_end),
        )
        log.info("Run %s: analyzed %d raw trips", run.id, general.total_trips)
        return self._with_step(
            run, StepType.ANALYZE_RAW,
            general_statistics=general,
            data_quality=result.data_quality,
            trip_anomalies=result.trip_anomalies,
            distributions=result.distributions,
            total_stations=result.station_analysis.total_stations,
            stations_key=f"{folder}/{_STATIONS_FILE}",
        )

    def station_activity(self, run: PipelineRunDto) -> list[StationActivityDto] | None:
        """Station table of the run's ANALYZE_RAW step; None if the step has not completed.

        Each station is marked as ours or outside the network by its id prefix,
        so the UI can show the network stations without knowing the prefixes.
        """
        step = next(s for s in run.steps if isinstance(s, AnalyzeRawStepDto))
        if step.stations_key is None:
            return None
        stations = pd.read_csv(
            self._data_root / step.stations_key, dtype={"station_id": str, "station_name": str},
        )
        return [
            StationActivityDto(
                station_id=row.station_id,
                station_name=None if pd.isna(row.station_name) else row.station_name,
                departures=row.departures,
                arrivals=row.arrivals,
                in_network=row.station_id.startswith(config.NETWORK_STATION_PREFIXES),
            )
            for row in stations.itertuples(index=False)
        ]

    def _load_raw_trips(self, run: PipelineRunDto) -> pd.DataFrame:
        """All trips downloaded by INGEST as one DataFrame, read once per run.

        ANALYZE_RAW and PROCESS get this same object; neither may change it.
        """
        if run.id not in self._raw_trips:
            ingest = next(s for s in run.steps if isinstance(s, IngestStepDto))
            frames = [read_raw_trips_csv(self._data_root / file.key) for file in ingest.files]
            self._raw_trips[run.id] = pd.concat(frames, ignore_index=True)
            log.info("Run %s: read %d raw trips from %d files", run.id, len(self._raw_trips[run.id]), len(frames))
        return self._raw_trips[run.id]

    def _handle_not_wired_step(self, run: PipelineRunDto) -> PipelineRunDto:
        """Placeholder for steps whose input from earlier steps is not wired yet."""
        raise NotImplementedError("This step is not wired into the pipeline yet")

