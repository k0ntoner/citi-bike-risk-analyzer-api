"""Schema of the raw trip dataset: the input of ANALYZE_RAW and PROCESS.

One row per trip, exactly as Citi Bike publishes it, with fixed column types.
The schema describes structure only (columns, types, missing values); checking
the values themselves (long rides, unknown bike types, ...) is the job of the
analysis and processing steps.

Use the column constants instead of string literals::

    from bikeflow.contracts.raw_trips import RawTrip

    df[RawTrip.STARTED_AT]

See ``docs/data-notes.md`` for what is known about the values.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from os import PathLike
from typing import IO

import pandas as pd
from pandas.api import types


class RawTrip:
    """Column names of the raw trip dataset."""

    RIDE_ID = "ride_id"
    RIDEABLE_TYPE = "rideable_type"
    STARTED_AT = "started_at"
    ENDED_AT = "ended_at"
    START_STATION_NAME = "start_station_name"
    START_STATION_ID = "start_station_id"
    END_STATION_NAME = "end_station_name"
    END_STATION_ID = "end_station_id"
    START_LAT = "start_lat"
    START_LNG = "start_lng"
    END_LAT = "end_lat"
    END_LNG = "end_lng"
    MEMBER_CASUAL = "member_casual"


class Kind(StrEnum):
    """Allowed column types."""

    STRING = "string"
    DATETIME = "naive datetime"
    FLOAT = "float"


_KIND_CHECKS: dict[Kind, Callable[[pd.Series], bool]] = {
    Kind.STRING: types.is_string_dtype,
    # is_datetime64_dtype is False for tz-aware columns, so this also rejects a zone.
    Kind.DATETIME: types.is_datetime64_dtype,
    Kind.FLOAT: types.is_float_dtype,
}


@dataclass(frozen=True)
class Column:
    """Contract of one column.

    Attributes:
        name: Column name.
        kind: Column type.
        nullable: Whether missing values are allowed.
        description: Meaning of the column.
    """

    name: str
    kind: Kind
    nullable: bool
    description: str


COLUMNS: tuple[Column, ...] = (
    Column(RawTrip.RIDE_ID, Kind.STRING, False, "Unique trip id, 16 hex chars."),
    Column(RawTrip.RIDEABLE_TYPE, Kind.STRING, False, "Bike type: classic_bike or electric_bike."),
    Column(RawTrip.STARTED_AT, Kind.DATETIME, False, "Rental start, local New York time without zone."),
    Column(RawTrip.ENDED_AT, Kind.DATETIME, False, "Return time, local New York time without zone."),
    Column(RawTrip.START_STATION_NAME, Kind.STRING, False, "Start station name."),
    Column(RawTrip.START_STATION_ID, Kind.STRING, False, "Start station id; matches GBFS short_name."),
    Column(RawTrip.END_STATION_NAME, Kind.STRING, True, "End station name; missing if not returned to a dock."),
    Column(RawTrip.END_STATION_ID, Kind.STRING, True, "End station id; missing if not returned to a dock."),
    Column(RawTrip.START_LAT, Kind.FLOAT, False, "Start latitude."),
    Column(RawTrip.START_LNG, Kind.FLOAT, False, "Start longitude."),
    Column(RawTrip.END_LAT, Kind.FLOAT, True, "End latitude; missing if the end is unknown."),
    Column(RawTrip.END_LNG, Kind.FLOAT, True, "End longitude; missing if the end is unknown."),
    Column(RawTrip.MEMBER_CASUAL, Kind.STRING, False, "Rider type: member or casual."),
)

COLUMN_NAMES: tuple[str, ...] = tuple(c.name for c in COLUMNS)


class SchemaError(ValueError):
    """The DataFrame does not match the raw trip schema.

    Attributes:
        problems: Every mismatch found, one message each.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__("Raw trips do not match the schema:\n- " + "\n- ".join(problems))
        self.problems = problems


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Check that ``df`` matches the schema and return it unchanged.

    Extra columns are not allowed, so a mistake in a column name is caught here
    and not later inside a step.

    Raises:
        SchemaError: With all mismatches found, not only the first one.
    """
    problems = []
    missing = [name for name in COLUMN_NAMES if name not in df.columns]
    extra = [name for name in df.columns if name not in COLUMN_NAMES]
    if missing:
        problems.append(f"missing columns: {missing}")
    if extra:
        problems.append(f"unexpected columns: {extra}")

    for column in COLUMNS:
        if column.name not in df.columns:
            continue
        s = df[column.name]
        if not _KIND_CHECKS[column.kind](s):
            problems.append(f"{column.name}: expected {column.kind}, got {s.dtype}")
        if not column.nullable and (nulls := int(s.isna().sum())):
            problems.append(f"{column.name}: {nulls} missing values in a non-nullable column")

    if problems:
        raise SchemaError(problems)
    return df


def read_raw_trips_csv(source: str | PathLike | IO) -> pd.DataFrame:
    """Read a Citi Bike trip CSV (plain or zipped) into the schema and validate it.

    Station ids are read as strings explicitly: ids like ``5114.08`` would
    otherwise become floats.
    """
    datetime_columns = [c.name for c in COLUMNS if c.kind == Kind.DATETIME]
    dtypes = {c.name: "str" if c.kind == Kind.STRING else "float64" for c in COLUMNS if c.kind != Kind.DATETIME}
    df = pd.read_csv(source, usecols=list(COLUMN_NAMES), dtype=dtypes)
    for name in datetime_columns:
        df[name] = pd.to_datetime(df[name], format="%Y-%m-%d %H:%M:%S.%f")
    return validate(df[list(COLUMN_NAMES)])
