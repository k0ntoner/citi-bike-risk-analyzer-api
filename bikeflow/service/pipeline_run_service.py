"""Service layer over pipeline runs: DTOs in, DTOs out."""

from sqlalchemy.orm import Session, sessionmaker

from bikeflow.service.dto import PipelineRunDto
from bikeflow.service.mapper import apply_run, run_to_dto
from bikeflow.storage.models import PipelineRun, PipelineStep, StepType, Trigger
from bikeflow.storage.repositories import PipelineRunRepository


class RunNotFoundError(LookupError):
    """Raised when a run with the given id does not exist."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"Pipeline run {run_id} not found")
        self.run_id = run_id


class PipelineRunService:
    """Reads and writes pipeline runs with their steps.

    Each method runs in its own transaction: committed on success,
    rolled back on any exception.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def find_by_id(self, run_id: int) -> PipelineRunDto | None:
        """Return the run with its steps, or None if it does not exist."""
        with self._session_factory.begin() as session:
            run = PipelineRunRepository(session).find_by_id(run_id)
            return run_to_dto(run) if run else None

    def create(self, trigger: Trigger, window_months: int) -> PipelineRunDto:
        """Create a new run with one PENDING step of every type and save it.

        The status, timestamps and ids are set by the database.

        Args:
            trigger: Who starts the run.
            window_months: Requested length of the trip data window, in months.

        Returns:
            The saved run with its steps in execution order.
        """
        with self._session_factory.begin() as session:
            run = PipelineRun(
                trigger=trigger,
                window_months=window_months,
                steps=[PipelineStep(step_type=step_type) for step_type in StepType],
            )
            PipelineRunRepository(session).save(run)
            return run_to_dto(run)

    def update(self, dto: PipelineRunDto) -> PipelineRunDto:
        """Save the changes of an existing run and its steps.

        Returns:
            The saved state with database-generated values (``updated_at``,
            ids of new steps) filled in.

        Raises:
            ValueError: ``dto.id`` is None, i.e. the run was never created.
            RunNotFoundError: No run with ``dto.id`` exists.
        """
        if dto.id is None:
            raise ValueError("Cannot update a run without id; use create() for a new run")
        with self._session_factory.begin() as session:
            repository = PipelineRunRepository(session)
            run = repository.find_by_id(dto.id)
            if run is None:
                raise RunNotFoundError(dto.id)
            apply_run(dto, run)
            repository.save(run)
            return run_to_dto(run)

    def delete(self, run_id: int) -> bool:
        """Delete the run with its steps; return False if it did not exist."""
        with self._session_factory.begin() as session:
            repository = PipelineRunRepository(session)
            run = repository.find_by_id(run_id)
            if run is None:
                return False
            repository.delete(run)
            return True
