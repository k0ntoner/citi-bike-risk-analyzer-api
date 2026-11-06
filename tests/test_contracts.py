"""Validation built into the step contracts."""

import io

import pytest

from bikeflow import config
from bikeflow.contracts.analyze_raw import AnalyzeRawInput
from bikeflow.contracts.ingest import IngestInput, IngestResult
from bikeflow.contracts.process import ProcessInput
from bikeflow.contracts.raw_trips import RawTrip, SchemaError, read_raw_trips_csv
from bikeflow.storage.dataset_store import LocalDatasetStore
from tests.test_raw_trips import CSV


@pytest.mark.parametrize("contract", [AnalyzeRawInput, ProcessInput])
def test_step_inputs_accept_valid_trips_and_reject_invalid(contract):
    trips = read_raw_trips_csv(io.StringIO(CSV))

    assert contract(trips).trips is trips  # the same object, not a copy
    with pytest.raises(SchemaError):
        contract(trips.drop(columns=[RawTrip.RIDE_ID]))


def test_ingest_input_validation(tmp_path):
    store = LocalDatasetStore(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        IngestInput(0, config.TRIPDATA_FILE_REGEX, store)
    with pytest.raises(ValueError, match="month"):
        IngestInput(12, r"^JC-(\d{6})\.zip$", store)


def test_ingest_result_cannot_be_empty():
    with pytest.raises(ValueError):
        IngestResult(files=(), period_start=None, period_end=None)
