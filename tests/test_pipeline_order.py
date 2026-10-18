"""The orchestrator executes steps in pipeline order, whatever order they are loaded in."""

import random

from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.dto import PipelineRunDto
from bikeflow.service.mapper import _STEP_DTO_BY_TYPE
from bikeflow.storage.models import RunStatus, StepStatus, StepType, Trigger


class ShuffledRuns:
    """Run service that returns the steps of a new run in random order."""

    def create(self, trigger, window_months):
        steps = [_STEP_DTO_BY_TYPE[t]() for t in StepType]
        random.Random(0).shuffle(steps)
        assert [s.step_type for s in steps] != list(StepType)
        return PipelineRunDto(id=1, trigger=trigger, window_months=window_months, steps=steps)

    def update(self, dto):
        return dto


def test_steps_run_in_pipeline_order():
    pipeline = BikeFlowPipelineService(ShuffledRuns(), None, None, None, None, None)
    executed = []

    def recorder(step_type):
        def handle(run):
            executed.append(step_type)
            return run
        return handle

    pipeline._handlers = {t: recorder(t) for t in StepType}

    run = pipeline.run(Trigger.MANUAL)

    assert executed == sorted(StepType, key=lambda t: t.order)
    assert run.status == RunStatus.COMPLETED
    assert all(s.status == StepStatus.COMPLETED for s in run.steps)
