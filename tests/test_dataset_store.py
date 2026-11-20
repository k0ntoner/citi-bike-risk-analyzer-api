import pytest

from bikeflow.contracts.dataset_store import DatasetStore
from bikeflow.storage.dataset_store import LocalDatasetStore


def test_write_then_read(tmp_path):
    store: DatasetStore = LocalDatasetStore(tmp_path / "steps" / "ingest")

    with store.open_write("JC-202609.csv.zip") as f:
        f.write(b"data")

    assert store.exists("JC-202609.csv.zip")
    with store.open_read("JC-202609.csv.zip") as f:
        assert f.read() == b"data"


def test_failed_write_leaves_nothing(tmp_path):
    store = LocalDatasetStore(tmp_path)

    with pytest.raises(ConnectionError):
        with store.open_write("JC-202609.csv.zip") as f:
            f.write(b"partial")
            raise ConnectionError("download interrupted")

    assert not store.exists("JC-202609.csv.zip")
    assert list(tmp_path.iterdir()) == []  # no temporary file left either


def test_overwrite_replaces_file(tmp_path):
    store = LocalDatasetStore(tmp_path)
    for content in (b"old", b"new"):
        with store.open_write("a.csv") as f:
            f.write(content)

    with store.open_read("a.csv") as f:
        assert f.read() == b"new"


@pytest.mark.parametrize("key", ["../outside.csv", "/etc/passwd", "a/../../b", ""])
def test_rejects_keys_outside_root(tmp_path, key):
    with pytest.raises(ValueError):
        LocalDatasetStore(tmp_path).exists(key)


def test_read_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        with LocalDatasetStore(tmp_path).open_read("missing.csv"):
            pass
