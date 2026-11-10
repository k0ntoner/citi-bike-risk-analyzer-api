"""Repositories over the ORM entities.

Repositories never commit: the caller owns the transaction, e.g.::

    with SessionLocal.begin() as session:
        runs = PipelineRunRepository(session)
        run = runs.find_by_id(run_id)
        ...
        runs.save(run)
"""

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from bikeflow.storage.models import PipelineRun


class PipelineRunRepository:
    """Persistence for ``PipelineRun`` together with its steps.

    The run is the aggregate root: steps are never saved or deleted on their own.
    Add a step to ``run.steps`` or change a loaded step, then save the run;
    the cascade writes the steps in the same flush.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_id(self, run_id: int) -> PipelineRun | None:
        """Return the run with its steps loaded, or None if it does not exist."""
        return self._session.get(PipelineRun, run_id, options=[selectinload(PipelineRun.steps)])

    def save(self, run: PipelineRun) -> PipelineRun:
        """Insert a new run or update an existing one, with all its steps.

        Flushes immediately, so database-generated values (``id``, ``created_at``,
        default statuses) are available on the returned object.
        """
        if run.id is not None:
            # Force an UPDATE of the run row even if only its steps changed,
            # so ``updated_at`` reflects any change in the aggregate.
            run.updated_at = func.now()
        self._session.add(run)
        self._session.flush()
        return run

    def delete(self, run: PipelineRun) -> None:
        """Delete the run; its steps are removed by ``ON DELETE CASCADE``."""
        self._session.delete(run)
        self._session.flush()
