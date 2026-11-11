"""INGEST cache: a source file version (ETag) is downloaded once and reused by later runs."""

import pytest

from bikeflow import config
from bikeflow.contracts.ingest import IngestInput
from bikeflow.steps.ingest.source import SourceFile
from bikeflow.steps.ingest_service import IncompleteDownloadError, IngestService
from bikeflow.storage.dataset_store import LocalDatasetStore
from tests.test_ingest_inflow import NETWORK, nyc, source
from tests.test_ingest_service import jc

INFLOW = "202609-citibike-tripdata-inflow.csv"


class CountingSource:
    """Wraps a fake source: counts downloads and can publish a new version of a file."""

    def __init__(self, fake):
        self.fake = fake
        self.opened = []
        self.etags = {}

    def list_files(self):
        return [
            SourceFile(f.name, f.url, self.etags.get(f.name, f.etag), f.size)
            for f in self.fake.list_files()
        ]

    def open(self, file):
        self.opened.append(file.name)
        return self.fake.open(file)


def ingest(tmp_path, src, run, prefixes=NETWORK):
    """One run: its own target folder, the cache shared with other runs."""
    target = LocalDatasetStore(tmp_path / run)
    cache = LocalDatasetStore(tmp_path / "cache")
    step_input = IngestInput(
        12, config.TRIPDATA_FILE_REGEX, target, config.INFLOW_FILE_REGEX, prefixes, cache,
    )
    return IngestService(src).ingest(step_input), target


@pytest.fixture
def src():
    return CountingSource(source(["202609"], ["202609"]))


def test_second_run_takes_everything_from_the_cache(tmp_path, src):
    first, first_store = ingest(tmp_path, src, "run-1")
    src.opened.clear()
    second, second_store = ingest(tmp_path, src, "run-2")

    assert src.opened == []  # nothing downloaded
    assert [f.cached for f in first.files] == [False, False]
    assert [f.cached for f in second.files] == [True, True]
    # Same files and counts as the first run, in the run's own folder.
    assert [(f.key, f.size, f.scanned_trips, f.kept_trips) for f in second.files] == [
        (f.key, f.size, f.scanned_trips, f.kept_trips) for f in first.files
    ]
    for key in (jc("202609"), INFLOW):
        with first_store.open_read(key) as a, second_store.open_read(key) as b:
            assert a.read() == b.read()


def test_republished_file_is_downloaded_again(tmp_path, src):
    ingest(tmp_path, src, "run-1")
    src.opened.clear()
    src.etags[nyc("202609")] = "new-version"

    result, _ = ingest(tmp_path, src, "run-2")

    assert src.opened == [nyc("202609")]
    assert [f.cached for f in result.files] == [True, False]


def test_other_network_prefixes_extract_the_inflow_again(tmp_path, src):
    ingest(tmp_path, src, "run-1")
    src.opened.clear()

    result, _ = ingest(tmp_path, src, "run-2", prefixes=("JC",))

    assert src.opened == [nyc("202609")]
    assert result.files[1].kept_trips == 1  # only the trip to JC076, not the one to HB201


def test_failed_download_is_not_cached(tmp_path, src):
    src.fake.broken.add(jc("202609"))
    with pytest.raises(IncompleteDownloadError):
        ingest(tmp_path, src, "run-1")
    src.fake.broken.clear()
    src.opened.clear()

    result, _ = ingest(tmp_path, src, "run-2")

    assert jc("202609") in src.opened
    assert not result.files[0].cached


def test_etag_with_unsafe_characters_is_a_valid_folder(tmp_path, src):
    src.etags[jc("202609")] = "../a/b"
    ingest(tmp_path, src, "run-1")
    assert (tmp_path / "cache" / "___a_b" / jc("202609")).is_file()
