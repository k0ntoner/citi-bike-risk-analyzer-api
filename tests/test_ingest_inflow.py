"""INGEST with the inflow: trips from New York City into our network."""

import io
import logging
import zipfile
from datetime import date

import pandas as pd
import pytest

from bikeflow import config
from bikeflow.contracts.ingest import IngestInput
from bikeflow.contracts.raw_trips import RawTrip, read_raw_trips_csv
from bikeflow.steps.ingest.inflow import extract_inflow
from bikeflow.steps.ingest_service import IngestService, NoTripFilesError
from bikeflow.storage.dataset_store import LocalDatasetStore
from tests.test_ingest_service import FakeSource, jc

HEADER = (
    "ride_id,rideable_type,started_at,ended_at,start_station_name,start_station_id,"
    "end_station_name,end_station_id,start_lat,start_lng,end_lat,end_lng,member_casual\n"
)
# The NYC file as published: 19-digit ride ids, no quotes, split into several CSV parts.
PART1 = HEADER + (
    "92066070453527473,electric_bike,2026-10-12 17:39:07.249,2026-10-12 18:19:52.697,"
    "W 34 St & 11 Ave,6578.01,Dixon Mills,JC076,40.75594159,-74.0021163,40.7216301,-74.0499678,member\n"
    "2506859557764627159,classic_bike,2026-10-01 17:52:36.306,2026-10-01 17:54:24.322,"
    "W 24 St & 7 Ave,6257.03,8 Ave & W 24 St,6224.06,40.74487634,-73.99529885,40.7459107,-73.9980711,member\n"
)
PART2 = HEADER + (
    "5102585189745624492,electric_bike,2026-10-13 17:51:42.913,2026-10-13 18:19:51.673,"
    "Columbus Ave & W 95 St,7520.07,12 St & Sinatra Dr N,HB201,40.7919557,-73.968087,40.7506041,-74.0240201,casual\n"
    "818657285427243502,electric_bike,2026-10-14 15:44:52.832,2026-10-14 15:50:34.994,"
    "E 40 St & 5 Ave,6474.11,,,40.752062307,-73.9816324043,,,member\n"
)
NETWORK = ("JC", "HB")


def nyc(yyyymm):
    return f"{yyyymm}-citibike-tripdata.zip"


def nyc_zip(*parts):
    """A zipped NYC month: CSV parts plus the macOS metadata entry real archives have."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number, part in enumerate(parts, start=1):
            archive.writestr(f"202610-citibike-tripdata_{number}.csv", part)
        archive.writestr("__MACOSX/._202610-citibike-tripdata_1.csv", b"junk")
    return buffer.getvalue()


def source(network_months, inflow_months, archive=None):
    """Fake bucket with JC files and NYC archives of the given months."""
    fake = FakeSource([jc(m) for m in network_months])
    for month in inflow_months:
        fake.contents[nyc(month)] = archive or nyc_zip(PART1, PART2)
    return fake


def ingest(tmp_path, fake, window_months=12):
    store = LocalDatasetStore(tmp_path)
    step_input = IngestInput(
        window_months, config.TRIPDATA_FILE_REGEX, store, config.INFLOW_FILE_REGEX, NETWORK,
    )
    return IngestService(fake).ingest(step_input), store


def test_keeps_only_trips_that_end_in_the_network(tmp_path):
    result, store = ingest(tmp_path, source(["202610"], ["202610"]))

    assert [f.key for f in result.files] == [jc("202610"), "202610-citibike-tripdata-inflow.csv"]
    inflow = result.files[1]
    assert (inflow.scanned_trips, inflow.kept_trips) == (4, 2)
    assert inflow.month == date(2026, 10, 1)
    assert inflow.source_url == f"https://example.test/{nyc('202610')}"
    assert result.files[0].scanned_trips is None  # our file is stored as is

    with store.open_read(inflow.key) as f:
        trips = read_raw_trips_csv(f)  # an ordinary trip file: reads and validates
    assert list(trips[RawTrip.END_STATION_ID]) == ["JC076", "HB201"]
    assert list(trips[RawTrip.RIDE_ID]) == ["92066070453527473", "5102585189745624492"]
    assert trips[RawTrip.STARTED_AT].iloc[0] == pd.Timestamp("2026-10-12 17:39:07.249")
    assert trips[RawTrip.START_STATION_ID].iloc[0] == "6578.01"  # stays a string


def test_window_takes_only_months_published_for_both_regions(tmp_path, caplog):
    fake = source(["202608", "202609", "202610"], ["202607", "202608", "202609"])
    with caplog.at_level(logging.WARNING):
        result, _ = ingest(tmp_path, fake)

    assert sorted({f.month for f in result.files}) == [date(2026, 8, 1), date(2026, 9, 1)]
    assert (result.period_start, result.period_end) == (date(2026, 8, 1), date(2026, 9, 30))
    assert "2026-10-01" in caplog.text  # the newest JC month waits for its NYC file


def test_window_counts_months_not_files(tmp_path):
    result, _ = ingest(tmp_path, source(["202609", "202610"], ["202609", "202610"]), window_months=1)
    assert [f.key for f in result.files] == [jc("202610"), "202610-citibike-tripdata-inflow.csv"]


def test_fails_when_no_month_is_published_for_both_regions(tmp_path):
    with pytest.raises(NoTripFilesError, match="both"):
        ingest(tmp_path, source(["202610"], ["202609"]))


def test_no_trips_into_the_network_gives_a_valid_empty_file(tmp_path):
    archive = nyc_zip(HEADER + PART1.splitlines(keepends=True)[2])  # only the NYC -> NYC trip
    result, store = ingest(tmp_path, source(["202610"], ["202610"], archive))

    assert (result.files[1].scanned_trips, result.files[1].kept_trips) == (1, 0)
    with store.open_read(result.files[1].key) as f:
        assert read_raw_trips_csv(f).empty


def test_extract_reads_in_chunks(monkeypatch):
    # Chunks of one row: the result must not depend on where a chunk ends.
    monkeypatch.setattr("bikeflow.steps.ingest.inflow._CHUNK_ROWS", 1)
    target = io.BytesIO()
    stats = extract_inflow(io.BytesIO(nyc_zip(PART1, PART2)), target, NETWORK)

    assert (stats.scanned_trips, stats.kept_trips) == (4, 2)
    assert target.getvalue().decode().count("ride_id") == 1  # one header only
    assert len(read_raw_trips_csv(io.BytesIO(target.getvalue()))) == 2


def test_extract_rejects_an_archive_without_csv():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "nothing here")
    with pytest.raises(ValueError, match="No CSV"):
        extract_inflow(buffer, io.BytesIO(), NETWORK)
