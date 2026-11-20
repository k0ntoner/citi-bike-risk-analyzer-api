"""S3HttpTripDataSource with a fake HTTP session: XML parsing, pagination, streaming."""

import io

import pytest
import requests

from bikeflow.steps.ingest.source import S3HttpTripDataSource, SourceFile

BUCKET = "https://s3.example.test/tripdata"


def listing(keys: list[str], truncated: bool) -> bytes:
    contents = "".join(
        f"<Contents><Key>{key}</Key><ETag>&quot;etag-{key}&quot;</ETag><Size>{len(key)}</Size></Contents>"
        for key in keys
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Name>tripdata</Name><IsTruncated>{str(truncated).lower()}</IsTruncated>{contents}"
        "</ListBucketResult>"
    ).encode()


class FakeResponse:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status
        self.raw = io.BytesIO(content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Serves listing pages by the ``marker`` parameter and file contents by URL."""

    def __init__(self, pages: dict[str, bytes], files: dict[str, bytes] | None = None):
        self.pages = pages
        self.files = files or {}
        self.markers = []

    def get(self, url, params=None, stream=False, timeout=None):
        if url == f"{BUCKET}/":
            self.markers.append(params["marker"])
            return FakeResponse(self.pages[params["marker"]])
        return FakeResponse(self.files[url]) if url in self.files else FakeResponse(status=404)


def test_list_files_parses_keys_etags_and_sizes():
    session = FakeSession({"": listing(["JC-202609-citibike-tripdata.csv.zip"], truncated=False)})

    files = S3HttpTripDataSource(BUCKET, session).list_files()

    assert files == [SourceFile(
        name="JC-202609-citibike-tripdata.csv.zip",
        url=f"{BUCKET}/JC-202609-citibike-tripdata.csv.zip",
        etag="etag-JC-202609-citibike-tripdata.csv.zip",  # quotes stripped
        size=len("JC-202609-citibike-tripdata.csv.zip"),
    )]


def test_list_files_follows_pagination():
    session = FakeSession({
        "": listing(["a.zip", "b.zip"], truncated=True),
        "b.zip": listing(["c.zip"], truncated=False),
    })

    files = S3HttpTripDataSource(BUCKET, session).list_files()

    assert [f.name for f in files] == ["a.zip", "b.zip", "c.zip"]
    assert session.markers == ["", "b.zip"]  # next page starts after the last key


def test_open_streams_the_file():
    url = f"{BUCKET}/a.zip"
    source = S3HttpTripDataSource(BUCKET, FakeSession({}, files={url: b"zip bytes"}))

    with source.open(SourceFile("a.zip", url, "e", 9)) as stream:
        assert stream.read() == b"zip bytes"


def test_http_errors_are_raised():
    source = S3HttpTripDataSource(BUCKET, FakeSession({}))
    with pytest.raises(requests.HTTPError):
        with source.open(SourceFile("missing.zip", f"{BUCKET}/missing.zip", "e", 1)):
            pass
