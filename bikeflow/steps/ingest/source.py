"""Source of the trip history files."""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import requests

_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


@dataclass(frozen=True)
class SourceFile:
    """A file published in the source.

    Attributes:
        name: File name (S3 key), e.g. ``JC-202609-citibike-tripdata.csv.zip``.
        url: Where to download it from.
        etag: Content version reported by the source.
        size: Size in bytes reported by the source.
    """

    name: str
    url: str
    etag: str
    size: int


class TripDataSource(Protocol):
    """Where the INGEST step gets the trip files from; replaced by a fake in tests."""

    def list_files(self) -> list[SourceFile]:
        """All files currently published."""
        ...

    def open(self, file: SourceFile) -> AbstractContextManager[BinaryIO]:
        """Stream of the file contents, read in chunks without loading it whole."""
        ...


class S3HttpTripDataSource:
    """Public S3 bucket read over plain HTTP; needs no AWS credentials.

    Args:
        bucket_url: Bucket URL, e.g. ``https://s3.amazonaws.com/tripdata``.
        session: HTTP session to reuse connections; a new one if not given.
        timeout: Seconds to wait for the server to connect or send data.
    """

    def __init__(self, bucket_url: str, session: requests.Session | None = None, timeout: float = 60) -> None:
        self._bucket_url = bucket_url.rstrip("/")
        self._session = session or requests.Session()
        self._timeout = timeout

    def list_files(self) -> list[SourceFile]:
        """List the bucket, following pagination (S3 returns up to 1000 keys per page)."""
        files: list[SourceFile] = []
        marker = ""
        while True:
            response = self._session.get(f"{self._bucket_url}/", params={"marker": marker}, timeout=self._timeout)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall("s3:Contents", _S3_NS):
                name = item.findtext("s3:Key", namespaces=_S3_NS)
                files.append(SourceFile(
                    name=name,
                    url=f"{self._bucket_url}/{name}",
                    etag=item.findtext("s3:ETag", namespaces=_S3_NS).strip('"'),
                    size=int(item.findtext("s3:Size", namespaces=_S3_NS)),
                ))
            if root.findtext("s3:IsTruncated", namespaces=_S3_NS) != "true" or not files:
                return files
            marker = files[-1].name

    @contextmanager
    def open(self, file: SourceFile) -> Iterator[BinaryIO]:
        """Stream the file over HTTP."""
        with self._session.get(file.url, stream=True, timeout=self._timeout) as response:
            response.raise_for_status()
            response.raw.decode_content = True  # undo transfer compression, if any
            yield response.raw
