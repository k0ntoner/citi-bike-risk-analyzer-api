import io
from contextlib import contextmanager
from datetime import date

import pytest

from bikeflow import config
from bikeflow.contracts.ingest import IngestInput
from bikeflow.steps.ingest.source import SourceFile
from bikeflow.steps.ingest_service import IncompleteDownloadError, IngestService, NoTripFilesError
from bikeflow.storage.dataset_store import LocalDatasetStore


class FakeSource:
    """Serves in-memory files; ``broken`` files are cut off after half their size."""

    def __init__(self, names, broken=()):
        self.contents = {name: f"trips of {name}".encode() for name in names}
        self.broken = set(broken)

    def list_files(self):
        return [SourceFile(name, f"https://example.test/{name}", f"etag-{name}", len(data))
                for name, data in self.contents.items()]

    @contextmanager
    def open(self, file):
        data = self.contents[file.name]
        yield io.BytesIO(data[: len(data) // 2] if file.name in self.broken else data)


def jc(yyyymm):
    return f"JC-{yyyymm}-citibike-tripdata.csv.zip"


def ingest(tmp_path, source, window_months=12):
    store = LocalDatasetStore(tmp_path)
    result = IngestService(source).ingest(IngestInput(window_months, config.TRIPDATA_FILE_REGEX, store))
    return result, store


def test_downloads_latest_months_oldest_first(tmp_path):
    names = [jc(m) for m in ["202606", "202609", "202607", "202608"]]
    result, store = ingest(tmp_path, FakeSource(names), window_months=2)

    assert [f.key for f in result.files] == [jc("202608"), jc("202609")]
    assert [f.month for f in result.files] == [date(2026, 8, 1), date(2026, 9, 1)]
    assert (result.period_start, result.period_end) == (date(2026, 8, 1), date(2026, 9, 30))
    with store.open_read(jc("202609")) as f:
        assert f.read() == b"trips of " + jc("202609").encode()
    assert not store.exists(jc("202607"))


def test_ignores_files_not_matching_the_pattern(tmp_path):
    names = [jc("202609"), "202609-citibike-tripdata.zip", "JC-2026-citibike-tripdata.csv.zip", "index.html"]
    result, _ = ingest(tmp_path, FakeSource(names))
    assert [f.key for f in result.files] == [jc("202609")]


def test_takes_what_is_published_when_window_is_longer(tmp_path):
    result, _ = ingest(tmp_path, FakeSource([jc("202512"), jc("202601")]), window_months=12)
    assert len(result.files) == 2
    assert (result.period_start, result.period_end) == (date(2025, 12, 1), date(2026, 1, 31))


def test_records_provenance(tmp_path):
    result, _ = ingest(tmp_path, FakeSource([jc("202609")]))
    file = result.files[0]
    assert file.source_url == f"https://example.test/{jc('202609')}"
    assert file.etag == f"etag-{jc('202609')}"
    assert file.size == len(f"trips of {jc('202609')}")
    assert file.downloaded_at.tzinfo is not None


def test_fails_when_nothing_matches(tmp_path):
    with pytest.raises(NoTripFilesError):
        ingest(tmp_path, FakeSource(["202609-citibike-tripdata.zip"]))


def test_incomplete_download_fails_and_leaves_no_file(tmp_path):
    source = FakeSource([jc("202608"), jc("202609")], broken=[jc("202609")])
    with pytest.raises(IncompleteDownloadError):
        ingest(tmp_path, source)
    assert sorted(p.name for p in tmp_path.iterdir()) == [jc("202608")]


def test_accepts_names_with_and_without_csv_extension(tmp_path):
    names = [jc("202509"), "JC-202510-citibike-tripdata.zip", jc("202511")]
    result, _ = ingest(tmp_path, FakeSource(names))
    assert [f.month for f in result.files] == [date(2025, 9, 1), date(2025, 10, 1), date(2025, 11, 1)]
