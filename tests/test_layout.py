from datetime import datetime, timedelta, timezone

import pytest

from bikeflow.storage.layout import run_dir, step_dir
from bikeflow.storage.models import StepType

KYIV_SUMMER = timezone(timedelta(hours=3))


def test_run_dir_uses_utc():
    created_at = datetime(2026, 10, 18, 18, 30, 0, tzinfo=KYIV_SUMMER)
    assert run_dir(42, created_at) == "runs/2026-10-18_15-30-00Z_run-42"


def test_run_dirs_sort_by_time():
    times = [datetime(2026, m, d, h, tzinfo=timezone.utc) for m, d, h in [(9, 5, 23), (10, 1, 6), (10, 18, 15)]]
    names = [run_dir(run_id, t) for run_id, t in zip([120, 7, 42], times)]
    assert sorted(names) == names


def test_run_dir_rejects_naive_time():
    with pytest.raises(ValueError):
        run_dir(1, datetime(2026, 10, 18, 15, 30))


def test_step_dir():
    run_folder = "runs/2026-10-18_15-30-00Z_run-42"
    assert step_dir(run_folder, StepType.INGEST) == f"{run_folder}/steps/ingest"
    assert step_dir(run_folder, StepType.ANALYZE_FEATURES) == f"{run_folder}/steps/analyze_features"
