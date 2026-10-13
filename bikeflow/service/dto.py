"""DTOs of the service layer.

Everything outside ``bikeflow.storage`` (orchestrator, API) works with these
classes instead of the ORM entities. They are immutable: to change a value,
create a copy with ``dto.model_copy(update={...})``.

Step-specific fields of each ``*StepDto`` are what gets stored in the
``step_data`` JSON column of ``pipeline_step``.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger


class _Dto(BaseModel):
    """Common settings: immutable, unknown fields are an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class StepDto(_Dto):
    """Fields shared by every pipeline step. Abstract: use one of the subclasses.

    Attributes:
        id: Step id; None until the step is saved.
        step_type: Discriminator that selects the concrete subclass.
        status: Step state.
        started_at: When the step began.
        completed_at: When the step finished, successfully or not.
        error_message: Why the step failed; None unless status is FAILED.
    """

    id: int | None = None
    step_type: StepType
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class IngestStepDto(StepDto):
    """Download of the monthly trip datasets.

    Attributes:
        period_start: First day of the earliest downloaded month.
        period_end: Last day of the latest downloaded month.
    """

    step_type: Literal[StepType.INGEST] = StepType.INGEST
    period_start: date | None = None
    period_end: date | None = None


class AnalyzeRawStepDto(StepDto):
    """Statistics of the raw trip dataset for the UI: anomalies, stations, distributions."""

    step_type: Literal[StepType.ANALYZE_RAW] = StepType.ANALYZE_RAW


class ProcessStepDto(StepDto):
    """Cleaning and aggregation into the training dataset."""

    step_type: Literal[StepType.PROCESS] = StepType.PROCESS


class AnalyzeFeaturesStepDto(StepDto):
    """Statistics of the train-ready dataset for the UI: feature distributions, correlations."""

    step_type: Literal[StepType.ANALYZE_FEATURES] = StepType.ANALYZE_FEATURES


class TrainStepDto(StepDto):
    """Model training and evaluation."""

    step_type: Literal[StepType.TRAIN] = StepType.TRAIN


AnyStepDto = Annotated[
    IngestStepDto | AnalyzeRawStepDto | ProcessStepDto | AnalyzeFeaturesStepDto | TrainStepDto,
    Field(discriminator="step_type"),
]
"""Any concrete step; Pydantic picks the subclass by ``step_type``."""


class PipelineRunDto(_Dto):
    """One execution of the pipeline with its steps.

    Attributes:
        id: Run id; None until the run is saved.
        status: Overall state of the run.
        trigger: Whether the run was started manually or by the scheduler.
        window_months: Requested length of the trip data window, in months.
        created_at: When the run was created.
        updated_at: Last change of the run or any of its steps.
        started_at: When the first step began.
        completed_at: When the run finished, successfully or not.
        steps: Steps in execution order.
    """

    id: int | None = None
    status: RunStatus = RunStatus.CREATED
    trigger: Trigger
    window_months: int = Field(gt=0)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    steps: list[AnyStepDto] = []
