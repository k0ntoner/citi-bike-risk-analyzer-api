"""Folder layout of run files in the data root.

Every run keeps its own copy of everything it produces, so older runs never
change when the source data or newer runs change::

    runs/2026-10-18_15-30-00Z_run-42/steps/ingest/JC-202609-citibike-tripdata.csv.zip
    runs/2026-10-18_15-30-00Z_run-42/steps/process/...
    cache/ingest/<etag>/JC-202609-citibike-tripdata.csv.zip   (shared by runs)

Paths are relative to the data root. Only the orchestrator uses this module;
steps get a ``DatasetStore`` already limited to their step folder.
"""

from datetime import datetime, timezone

from bikeflow.storage.models import StepType


def run_dir(run_id: int, created_at: datetime) -> str:
    """Folder of a run, e.g. ``runs/2026-10-18_15-30-00Z_run-42``.

    The creation time in UTC comes first, so sorting the names sorts the runs
    by time; the id keeps names unique.

    Raises:
        ValueError: ``created_at`` has no time zone.
    """
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    utc = created_at.astimezone(timezone.utc)
    return f"runs/{utc:%Y-%m-%d_%H-%M-%S}Z_run-{run_id}"


def cache_dir(step_type: StepType) -> str:
    """Folder shared by all runs where a step keeps reusable files, e.g. ``cache/ingest``.

    Runs still get their own copies in their step folders; the cache only
    saves downloading the same source file again.
    """
    return f"cache/{step_type.value.lower()}"


def step_dir(run_folder: str, step_type: StepType) -> str:
    """Folder of one step of a run, e.g. ``runs/.../steps/ingest``."""
    return f"{run_folder}/steps/{step_type.value.lower()}"
