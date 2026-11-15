"""INGEST step: download of the monthly trip datasets."""

import calendar
import dataclasses
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import BinaryIO

from bikeflow.contracts.dataset_store import DatasetStore
from bikeflow.contracts.ingest import IngestedFile, IngestInput, IngestResult
from bikeflow.steps.ingest.inflow import InflowStats, extract_inflow
from bikeflow.steps.ingest.source import SourceFile, TripDataSource

log = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024


class NoTripFilesError(LookupError):
    """The source has no file matching the file name pattern."""


class IncompleteDownloadError(IOError):
    """A downloaded file is smaller or larger than the source reported."""


@dataclass(frozen=True)
class _Month:
    """Source files of one month of the window.

    Attributes:
        month: The month, as its first day.
        network: File of our network, stored as is.
        inflow: File of the neighbouring region to extract trips from; None
            when the inflow is not requested.
    """

    month: date
    network: SourceFile
    inflow: SourceFile | None


class IngestService:
    """Downloads the trip history files that fall into the rolling window.

    Lists the published monthly files in the source, picks the latest
    ``window_months`` of them by the month in their name and streams each one
    into the step folder of the run. If ``inflow_file_regex`` is given, also
    downloads the file of the neighbouring region (New York City) for every
    month and keeps from it only the trips that end in our network. With a
    ``cache``, only files whose ETag is not cached yet are downloaded, so a
    repeated run takes seconds and a new month downloads just that month. Reports the
    stored keys, where each file came from and the actual period, which is
    shorter than requested if fewer months are published. Progress is written
    to the log.

    Args:
        source: Where the files are published; a fake in tests.
    """

    def __init__(self, source: TripDataSource) -> None:
        self._source = source

    def ingest(self, step_input: IngestInput) -> IngestResult:
        """Download the files of the window.

        Raises:
            NoTripFilesError: No published file matches ``file_regex``, or no
                month is published for both regions.
            IncompleteDownloadError: A download ended early or was corrupted.
        """
        selected = self._select(self._source.list_files(), step_input)
        log.info(
            "Downloading %d of %d requested months: %s .. %s",
            len(selected), step_input.window_months, selected[0].month, selected[-1].month,
        )

        files = []
        for number, month in enumerate(selected, start=1):
            files.append(self._download(month.month, month.network, step_input))
            log.info(
                "Downloaded %d/%d: %s (%.1f MB)",
                number, len(selected), month.network.name, month.network.size / 2**20,
            )
            if month.inflow is not None:
                files.append(self._download_inflow(month.month, month.inflow, step_input))

        newest = selected[-1].month
        return IngestResult(
            files=tuple(files),
            period_start=selected[0].month,
            period_end=newest.replace(day=calendar.monthrange(newest.year, newest.month)[1]),
        )

    @staticmethod
    def _select(published: list[SourceFile], step_input: IngestInput) -> list[_Month]:
        """Files of the latest months, oldest first.

        With the inflow, only months published for both regions are taken, so
        every month of the window is complete.
        """
        network = _by_month(published, step_input.file_regex)
        if not network:
            raise NoTripFilesError(f"No published file matches {step_input.file_regex!r}")
        if step_input.inflow_file_regex is None:
            months = sorted(network)[-step_input.window_months:]
            return [_Month(m, network[m], None) for m in months]

        inflow = _by_month(published, step_input.inflow_file_regex)
        months = sorted(network.keys() & inflow.keys())[-step_input.window_months:]
        if not months:
            raise NoTripFilesError(
                f"No month is published for both {step_input.file_regex!r} and {step_input.inflow_file_regex!r}"
            )
        # Usually the newest month, when one region is published a few days before the other.
        skipped = sorted(m for m in network.keys() - inflow.keys() if m > months[0])
        if skipped:
            log.warning("Months without an inflow file are skipped: %s", ", ".join(map(str, skipped)))
        return [_Month(m, network[m], inflow[m]) for m in months]

    def _download(self, month: date, source_file: SourceFile, step_input: IngestInput) -> IngestedFile:
        """Store one file in the target as is: from the cache or downloaded."""
        key = source_file.name
        cache = step_input.cache
        cached = False
        if cache is None:
            with step_input.target.open_write(key) as dst:
                # Raising inside open_write discards the partial file.
                size = self._copy(source_file, dst)
        else:
            cache_key = f"{_cache_folder(source_file.etag)}/{key}"
            cached = cache.exists(cache_key)
            if not cached:
                with cache.open_write(cache_key) as dst:
                    self._copy(source_file, dst)
            size = _copy_stored(cache, cache_key, step_input.target, key)
        return IngestedFile(
            key=key,
            month=month,
            source_url=source_file.url,
            etag=source_file.etag,
            size=size,
            downloaded_at=datetime.now(timezone.utc),
            cached=cached,
        )

    def _download_inflow(self, month: date, source_file: SourceFile, step_input: IngestInput) -> IngestedFile:
        """Store the trips into our network from a neighbouring region file.

        The cache holds the extracted trips, not the archive: the key is the
        ETag of the archive plus the network prefixes, so changing the prefixes
        extracts again. Next to the CSV a small JSON keeps the counts.
        """
        key = f"{_stem(source_file.name)}-inflow.csv"
        cache = step_input.cache
        if cache is None:
            with step_input.target.open_write(key) as dst:
                stats = self._extract(source_file, dst, step_input)
                size = dst.tell()
            cached = False
        else:
            folder = f"{_cache_folder(source_file.etag)}-{'_'.join(step_input.network_station_prefixes)}"
            csv_key, stats_key = f"{folder}/{key}", f"{folder}/{_stem(key)}.json"
            # The JSON is written last, so its presence means the CSV is complete.
            cached = cache.exists(stats_key)
            if cached:
                with cache.open_read(stats_key) as f:
                    stats = InflowStats(**json.load(f))
                log.info("Inflow %s taken from the cache", key)
            else:
                with cache.open_write(csv_key) as dst:
                    stats = self._extract(source_file, dst, step_input)
                with cache.open_write(stats_key) as f:
                    f.write(json.dumps(dataclasses.asdict(stats)).encode())
            size = _copy_stored(cache, csv_key, step_input.target, key)
        return IngestedFile(
            key=key,
            month=month,
            source_url=source_file.url,
            etag=source_file.etag,
            size=size,
            downloaded_at=datetime.now(timezone.utc),
            scanned_trips=stats.scanned_trips,
            kept_trips=stats.kept_trips,
            cached=cached,
        )

    def _extract(self, source_file: SourceFile, dst: BinaryIO, step_input: IngestInput) -> InflowStats:
        """Download a neighbouring region archive and write the trips into our network to ``dst``.

        The archive (~1 GB) goes to a temporary file, which is deleted right
        after the extraction, so at most one such archive is on disk at a time.
        """
        log.info("Downloading inflow %s (%.1f MB)", source_file.name, source_file.size / 2**20)
        with tempfile.TemporaryFile() as archive:
            self._copy(source_file, archive)
            archive.seek(0)
            stats = extract_inflow(archive, dst, step_input.network_station_prefixes)
        log.info(
            "Extracted %s: kept %d of %d trips (they end in the network)",
            source_file.name, stats.kept_trips, stats.scanned_trips,
        )
        return stats

    def _copy(self, source_file: SourceFile, dst: BinaryIO) -> int:
        """Stream a source file into ``dst``, check its size and return it.

        Raises:
            IncompleteDownloadError: The size differs from what the source reported.
        """
        with self._source.open(source_file) as src:
            shutil.copyfileobj(src, dst, _CHUNK_SIZE)
        written = dst.tell()
        if written != source_file.size:
            raise IncompleteDownloadError(
                f"{source_file.name}: got {written} bytes, expected {source_file.size}"
            )
        return written


def _by_month(published: list[SourceFile], file_regex: str) -> dict[date, SourceFile]:
    """Files whose name matches the pattern, by the month in the name."""
    pattern = re.compile(file_regex)
    by_month: dict[date, SourceFile] = {}
    for source_file in published:
        match = pattern.fullmatch(source_file.name)
        if match:
            yyyymm = match["month"]
            by_month[date(int(yyyymm[:4]), int(yyyymm[4:]), 1)] = source_file
    return by_month


def _cache_folder(etag: str) -> str:
    """Cache folder of a source file version: its ETag, reduced to safe path characters."""
    return re.sub(r"[^A-Za-z0-9-]", "_", etag)


def _copy_stored(src: DatasetStore, src_key: str, dst: DatasetStore, dst_key: str) -> int:
    """Copy a stored file between stores and return its size in bytes."""
    with src.open_read(src_key) as f_in, dst.open_write(dst_key) as f_out:
        shutil.copyfileobj(f_in, f_out, _CHUNK_SIZE)
        return f_out.tell()


def _stem(name: str) -> str:
    """File name without ``.zip`` and ``.csv``: ``202610-citibike-tripdata.zip`` -> ``202610-citibike-tripdata``."""
    return name.removesuffix(".zip").removesuffix(".csv")
