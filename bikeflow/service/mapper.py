"""Mapping between ORM entities and DTOs.

Common step fields map to ``pipeline_step`` columns; the fields a concrete
``*StepDto`` adds on top of ``StepDto`` are stored in the ``step_data`` JSON.
"""

from bikeflow.service.dto import (
    AnalyzeFeaturesStepDto,
    AnalyzeRawStepDto,
    AnyStepDto,
    IngestStepDto,
    PipelineRunDto,
    ProcessStepDto,
    StepDto,
    TrainStepDto,
)
from bikeflow.storage.models import PipelineRun, PipelineStep, StepType

_STEP_DTO_BY_TYPE: dict[StepType, type[StepDto]] = {
    StepType.INGEST: IngestStepDto,
    StepType.ANALYZE_RAW: AnalyzeRawStepDto,
    StepType.PROCESS: ProcessStepDto,
    StepType.ANALYZE_FEATURES: AnalyzeFeaturesStepDto,
    StepType.TRAIN: TrainStepDto,
}

_COMMON_STEP_FIELDS = set(StepDto.model_fields)


def step_to_dto(step: PipelineStep) -> AnyStepDto:
    """Build the concrete step DTO, unpacking ``step_data`` into its fields."""
    dto_class = _STEP_DTO_BY_TYPE[step.step_type]
    return dto_class(
        id=step.id,
        step_type=step.step_type,
        status=step.status,
        started_at=step.started_at,
        completed_at=step.completed_at,
        error_message=step.error_message,
        **(step.step_data or {}),
    )


def run_to_dto(run: PipelineRun) -> PipelineRunDto:
    """Build the run DTO; ``run.steps`` must be loaded."""
    return PipelineRunDto(
        id=run.id,
        status=run.status,
        trigger=run.trigger,
        window_months=run.window_months,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        steps=[step_to_dto(step) for step in run.steps],
    )


def apply_step(dto: StepDto, step: PipelineStep) -> None:
    """Copy writable fields of the DTO onto the step entity."""
    step.step_type = dto.step_type
    step.status = dto.status
    step.started_at = dto.started_at
    step.completed_at = dto.completed_at
    step.error_message = dto.error_message
    step_data = dto.model_dump(mode="json", exclude=_COMMON_STEP_FIELDS)
    step.step_data = step_data or None


def apply_run(dto: PipelineRunDto, run: PipelineRun) -> None:
    """Copy writable fields of the DTO onto the run entity, steps included.

    Steps are matched by ``step_type`` (unique within a run): existing ones are
    updated, new ones are added, and steps missing from the DTO are removed.
    ``id``, ``created_at`` and ``updated_at`` are owned by the database and ignored.
    """
    run.status = dto.status
    run.trigger = dto.trigger
    run.window_months = dto.window_months
    run.started_at = dto.started_at
    run.completed_at = dto.completed_at

    existing = {step.step_type: step for step in run.steps}
    steps = []
    for step_dto in dto.steps:
        step = existing.get(step_dto.step_type) or PipelineStep()
        apply_step(step_dto, step)
        steps.append(step)
    run.steps = steps
