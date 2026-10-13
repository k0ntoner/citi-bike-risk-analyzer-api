"""Service layer over pipeline runs: DTOs in, DTOs out."""

from sqlalchemy.orm import Session, sessionmaker

from bikeflow.service.dto import PipelineRunDto
from bikeflow.service.mapper import apply_run, run_to_dto
from bikeflow.storage.models import PipelineRun
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

    def save(self, dto: PipelineRunDto) -> PipelineRunDto:
        """Create the run if ``dto.id`` is None, otherwise update it.

        Returns the saved state with database-generated values (ids,
        timestamps, defaults) filled in.

        Raises:
            RunNotFoundError: ``dto.id`` is set but no such run exists.
        """
        with self._session_factory.begin() as session:
            repository = PipelineRunRepository(session)
            if dto.id is None:
                run = PipelineRun(steps=[])
            else:
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
