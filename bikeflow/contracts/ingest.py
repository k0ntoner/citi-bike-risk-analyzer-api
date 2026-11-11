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
        inflow_file_regex: Pattern of the monthly files of the neighbouring
            region (New York City), with a named group ``month``. Trips that
            start there and end in our network are only in those files. Only
            such trips are kept from them. None: do not download them. When
            set, the window contains only the months published for both regions.
        network_station_prefixes: Station id prefixes of our network, e.g.
            ``("JC", "HB")``; required with ``inflow_file_regex``.
        cache: Storage shared by all runs, where downloaded files are kept by
            the ETag of their source file. A file whose ETag is already there
            is copied from it instead of downloaded; a republished file has a
            new ETag, so it is downloaded again. None: always download.
    """

    window_months: int
    file_regex: str
    target: DatasetStore
    inflow_file_regex: str | None = None
    network_station_prefixes: tuple[str, ...] = ()
    cache: DatasetStore | None = None

    def __post_init__(self) -> None:
        if self.window_months <= 0:
            raise ValueError(f"window_months must be positive, got {self.window_months}")
        if "month" not in re.compile(self.file_regex).groupindex:
            raise ValueError("file_regex must have a named group 'month'")
        if self.inflow_file_regex is not None:
            if "month" not in re.compile(self.inflow_file_regex).groupindex:
                raise ValueError("inflow_file_regex must have a named group 'month'")
            if not self.network_station_prefixes:
                raise ValueError("network_station_prefixes are required with inflow_file_regex")


@dataclass(frozen=True)
class IngestedFile:
    """One downloaded monthly file and where it came from.

    Every file, plain or extracted, is a trip file in the ``raw_trips`` schema
    and is read with ``read_raw_trips_csv``.

    Attributes:
        key: Key of the file in ``IngestInput.target``.
        month: Month of the trips, as its first day (the month in the source
            file name).
        source_url: URL the file was downloaded from.
        etag: ETag of the source file at download time (content version).
        size: Size of the stored file in bytes.
        downloaded_at: When the file was stored in the run (timezone-aware).
        scanned_trips: For a file extracted from a neighbouring region file:
            how many trips the source file had. None for a file stored as is.
        kept_trips: For an extracted file: how many of them end in our
            network and were stored. None for a file stored as is.
        cached: Taken from the cache, not downloaded in this run.
    """

    key: str
    month: date
    source_url: str
    etag: str
    size: int
    downloaded_at: datetime
    scanned_trips: int | None = None
    kept_trips: int | None = None
    cached: bool = False


@dataclass(frozen=True)
class IngestResult:
    """Output of ``IngestService.ingest``.

    Attributes:
        files: Downloaded files, oldest month first; within a month the file
            of our network comes before the inflow file. Never empty: the step
            fails when no file is found.
        period_start: First day of the oldest month.
        period_end: Last day of the newest month.
    """

    files: tuple[IngestedFile, ...]
    period_start: date
    period_end: date

    def __post_init__(self) -> None:
        if not self.files:
            raise ValueError("IngestResult must contain at least one file")
