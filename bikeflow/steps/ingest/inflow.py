"""Extraction of the trips that enter our network from a file of a neighbouring region.

Trips are published per region by where they start: a trip from Manhattan to
Hoboken is only in the New York City file. That file is ~50 times larger than
ours (~5 million trips a month), so it is read in chunks and only the trips
that end at our stations are kept.
"""

import io
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

import pandas as pd

from bikeflow.contracts.raw_trips import COLUMN_NAMES, RawTrip, is_trip_csv

# Rows read at once: ~100 MB in memory, whatever the size of the file.
_CHUNK_ROWS = 500_000


@dataclass(frozen=True)
class InflowStats:
    """What an extraction found.

    Attributes:
        scanned_trips: Trips in the source archive.
        kept_trips: Trips that end at a network station and were written.
    """

    scanned_trips: int
    kept_trips: int


def extract_inflow(archive: BinaryIO, target: BinaryIO, station_prefixes: tuple[str, ...]) -> InflowStats:
    """Copy the trips that end at a network station from a zipped trip file.

    Values are copied as text without parsing, exactly as published, so the
    result is an ordinary trip CSV: ``read_raw_trips_csv`` reads and validates
    it like any other file.

    Args:
        archive: Seekable zip archive with one or more trip CSV files.
        target: Where to write the kept trips as CSV with a header.
        station_prefixes: Station id prefixes of our network, e.g. ``("JC", "HB")``.

    Raises:
        ValueError: The archive contains no CSV file, or a CSV lacks a column
            of the raw trip schema.
    """
    scanned = kept = 0
    text = io.TextIOWrapper(target, encoding="utf-8", newline="")
    header = True
    with zipfile.ZipFile(archive) as zf:
        members = [name for name in zf.namelist() if is_trip_csv(name)]
        if not members:
            raise ValueError(f"No CSV file in the archive: {zf.namelist()}")
        for name in members:
            with zf.open(name) as f:
                # keep_default_na=False: a missing station stays "" and is written back as "".
                chunks = pd.read_csv(
                    f, usecols=list(COLUMN_NAMES), dtype=str, keep_default_na=False, chunksize=_CHUNK_ROWS,
                )
                for chunk in chunks:
                    scanned += len(chunk)
                    matched = chunk[chunk[RawTrip.END_STATION_ID].str.startswith(station_prefixes)]
                    matched[list(COLUMN_NAMES)].to_csv(text, index=False, header=header)
                    header = False
                    kept += len(matched)
    if header:  # no rows at all: still write the header, so the file is a valid empty trip CSV
        pd.DataFrame(columns=list(COLUMN_NAMES)).to_csv(text, index=False)
    text.flush()
    text.detach()  # leave the target open: its owner closes it
    return InflowStats(scanned_trips=scanned, kept_trips=kept)
