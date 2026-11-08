"""Contract of the INGEST step."""

import re
from dataclasses import dataclass
from datetime import date, datetime

from bikeflow.contracts.dataset_store import DatasetStore


@dataclass(frozen=True)
class IngestInput:
    """Input of ``IngestService.ingest``.

    Attributes:
        window_months: How many latest published months to download. If fewer
            are published, all of them are downloaded.
        file_regex: Pattern of the monthly file names in the source bucket,
            with a named group ``month`` (``YYYYMM``).
        target: Where to save the files: already limited to the folder of this
            step in the run, so keys are just file names.
    """

    window_months: int
    file_regex: str
    target: DatasetStore

    def __post_init__(self) -> None:
        if self.window_months <= 0:
            raise ValueError(f"window_months must be positive, got {self.window_months}")
        if "month" not in re.compile(self.file_regex).groupindex:
            raise ValueError("file_regex must have a named group 'month'")


@dataclass(frozen=True)
class IngestedFile:
    """One downloaded monthly file and where it came from.

    Attributes:
        key: Key of the file in ``IngestInput.target``.
        month: Month of the trips, as its first day (trips belong to the month
            they ended in).
        source_url: URL the file was downloaded from.
        etag: ETag of the source file at download time (content version).
        size: Size of the file in bytes.
        downloaded_at: When the download finished (timezone-aware).
    """

    key: str
    month: date
    source_url: str
    etag: str
    size: int
    downloaded_at: datetime


@dataclass(frozen=True)
class IngestResult:
    """Output of ``IngestService.ingest``.

    Attributes:
        files: Downloaded files, oldest month first. Never empty: the step fails
            when no file is found.
        period_start: First day of the oldest month.
        period_end: Last day of the newest month.
    """

    files: tuple[IngestedFile, ...]
    period_start: date
    period_end: date

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("IngestResult must contain at least one file")
