"""INGEST step: download of the monthly trip datasets."""

import calendar
import logging
import re
import shutil
from datetime import date, datetime, timezone

from bikeflow.contracts.ingest import IngestedFile, IngestInput, IngestResult
from bikeflow.steps.ingest.source import SourceFile, TripDataSource

log = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024


class NoTripFilesError(LookupError):
    """The source has no file matching the file name pattern."""


class IncompleteDownloadError(IOError):
    """A downloaded file is smaller or larger than the source reported."""


class IngestService:
    """Downloads the trip history files that fall into the rolling window.

    Lists the published monthly files in the source, picks the latest
    ``window_months`` of them by the month in their name and streams each one
    into the step folder of the run. Reports the stored keys, where each file
    came from and the actual period, which is shorter than requested if fewer
    months are published. Progress is written to the log.

    Args:
        source: Where the files are published; a fake in tests.
    """

    def __init__(self, source: TripDataSource) -> None:
        self._source = source

    def ingest(self, step_input: IngestInput) -> IngestResult:
        """Download the files of the window.

        Raises:
            NoTripFilesError: No published file matches ``file_regex``.
            IncompleteDownloadError: A download ended early or was corrupted.
        """
        selected = self._select(self._source.list_files(), step_input)
        log.info(
            "Downloading %d of %d requested months: %s .. %s",
            len(selected), step_input.window_months, selected[0][0], selected[-1][0],
        )

        files = []
        for number, (month, source_file) in enumerate(selected, start=1):
            files.append(self._download(month, source_file, step_input))
            log.info(
                "Downloaded %d/%d: %s (%.1f MB)",
                number, len(selected), source_file.name, source_file.size / 2**20,
            )

        newest = selected[-1][0]
        return IngestResult(
            files=tuple(files),
            period_start=selected[0][0],
            period_end=newest.replace(day=calendar.monthrange(newest.year, newest.month)[1]),
        )

    @staticmethod
    def _select(published: list[SourceFile], step_input: IngestInput) -> list[tuple[date, SourceFile]]:
        """Matching files of the latest months, oldest first."""
        pattern = re.compile(step_input.file_regex)
        by_month: dict[date, SourceFile] = {}
        for source_file in published:
            match = pattern.fullmatch(source_file.name)
            if match:
                yyyymm = match["month"]
                by_month[date(int(yyyymm[:4]), int(yyyymm[4:]), 1)] = source_file
        if not by_month:
            raise NoTripFilesError(f"No published file matches {step_input.file_regex!r}")
        return sorted(by_month.items())[-step_input.window_months:]

    def _download(self, month: date, source_file: SourceFile, step_input: IngestInput) -> IngestedFile:
        """Stream one file into the target store and check its size."""
        key = source_file.name
        with self._source.open(source_file) as src, step_input.target.open_write(key) as dst:
            shutil.copyfileobj(src, dst, _CHUNK_SIZE)
            written = dst.tell()
            if written != source_file.size:
                # Raising inside open_write discards the partial file.
                raise IncompleteDownloadError(
                    f"{source_file.name}: got {written} bytes, expected {source_file.size}"
                )
        return IngestedFile(
            key=key,
            month=month,
            source_url=source_file.url,
            etag=source_file.etag,
            size=written,
            downloaded_at=datetime.now(timezone.utc),
        )
