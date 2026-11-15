"""Contracts of the PROCESS step."""

from dataclasses import dataclass, field
from datetime import date
from math import isfinite
import pandas as pd

from bikeflow.contracts import raw_trips

WEATHER_FEATURE_COLUMNS = (
    "temperature_c",
    "precipitation_mm",
    "wind_speed_ms",
)

def validate_weather(weather: pd.DataFrame) -> pd.DataFrame:
    """Validate prepared regional weather without modifying it.

    One row per UTC hour start, shared by the JC/HB stations.
    Temperature (2 m, Celsius) and wind (10 m, m/s) refer to the hour start.
    Precipitation is the total in mm over [timestamp, timestamp + 1 hour).
    The source adapter must align these intervals before calling PROCESS.
    Missing feature values are allowed; the join excludes affected rows.
    """
    if not isinstance(weather, pd.DataFrame):
        raise ValueError("weather: expected a DataFrame")

    required = ["timestamp", *WEATHER_FEATURE_COLUMNS]
    if not weather.columns.is_unique or set(weather.columns) != set(required):
        raise ValueError(f"weather: columns must be {required}")

    times = weather["timestamp"]
    if (
        not isinstance(times.dtype, pd.DatetimeTZDtype)
        or str(times.dt.tz) != "UTC"
        or times.isna().any()
    ):
        raise ValueError("weather: non-missing UTC timestamps required")

    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("weather: timestamps must be hour starts")

    if times.duplicated().any():
        raise ValueError("weather: duplicate timestamps")

    for column in WEATHER_FEATURE_COLUMNS:
        values = weather[column]

        if not (
            pd.api.types.is_float_dtype(values.dtype)
            or pd.api.types.is_integer_dtype(values.dtype)
        ):
            raise ValueError(f"weather.{column}: real numeric values required")

        known = values.dropna()

        if not known.map(isfinite).all():
            raise ValueError(f"weather.{column}: finite values required")

        if column != "temperature_c" and known.lt(0).any():
            raise ValueError(f"weather.{column}: non-negative values required")

    return weather

def _validate_coverage_settings(
    loaded_months: tuple[date, ...],
    departure_buffer_hours: int,
) -> None:
    """Require explicit source months and a positive whole-hour buffer."""
    if not isinstance(loaded_months, tuple) or not loaded_months:
        raise ValueError("loaded_months must be a non-empty tuple")

    if any(type(month) is not date or month.day != 1 for month in loaded_months):
        raise ValueError(
            "loaded_months must contain first-of-month datetime.date values"
        )

    if type(departure_buffer_hours) is not int or departure_buffer_hours <= 0:
        raise ValueError("departure_buffer_hours must be a positive integer")


@dataclass(frozen=True)
class ProcessInput:
    """Input of ProcessService.process.

    trips follows the raw_trips schema and is shared with ANALYZE_RAW.
    loaded_months identifies source archives successfully read in full.
    weather contains prepared regional hourly weather.
    Both input DataFrames must be treated as read-only.

    departure_buffer_hours applies at the end of each continuous source block.
    """

    trips: pd.DataFrame
    loaded_months: tuple[date, ...]
    weather: pd.DataFrame
    departure_buffer_hours: int = 48

    def __post_init__(self) -> None:
        raw_trips.validate(self.trips)
        _validate_coverage_settings(
            self.loaded_months,
            self.departure_buffer_hours,
        )
        validate_weather(self.weather)

@dataclass(frozen=True)
class ProcessResult:
    """Training table, column manifest, removal counts and assumptions.

    dataset has unique station_id/timestamp keys with UTC hour starts.
    feature_columns explicitly selects model inputs, including categorical
    columns. timestamp is only a row key; targets cannot be features.

    dropped_rows counts removed original trip rows by mutually exclusive reason.
    loaded_months and departure_buffer_hours record the settings actually used.
    The dataset and dropped_rows mapping must be treated as read-only.
    """

    dataset: pd.DataFrame
    feature_columns: tuple[str, ...]
    dropped_rows: dict[str, int]
    loaded_months: tuple[date, ...]
    departure_buffer_hours: int
    weather_excluded_station_hours: int = 0

    target_columns: tuple[str, ...] = field(
        default=("departures", "arrivals"),
        init=False,
    )
    station_activity_assumption: str = field(
        default="all_input_stations_active",
        init=False,
    )
    coverage_policy: str = field(
        default="departure_buffer_approximation",
        init=False,
    )
    holiday_calendar: str = field(
        default="US_federal_actual_and_observed",
        init=False,
    )
    def __post_init__(self) -> None:
        _validate_coverage_settings(
            self.loaded_months,
            self.departure_buffer_hours,
        )
        if (
            type(self.weather_excluded_station_hours) is not int
            or self.weather_excluded_station_hours < 0
        ):
            raise ValueError(
                "weather_excluded_station_hours must be a non-negative integer"
            )
        features = self.feature_columns
        if (
            not isinstance(features, tuple)
            or not features
            or any(
                not isinstance(column, str) or not column
                for column in features
            )
            or len(set(features)) != len(features)
        ):
            raise ValueError(
                "feature_columns must be a non-empty tuple of unique names"
            )

        if set(features) & {"timestamp", *self.target_columns}:
            raise ValueError(
                "feature_columns must not contain timestamp or targets"
            )

        frame = self.dataset
        keys = ["station_id", "timestamp"]
        required = set(keys) | set(features) | set(self.target_columns)

        if not frame.columns.is_unique:
            raise ValueError("dataset: duplicate column names")

        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"dataset: missing columns {sorted(missing)}")

        if frame[list(required)].isna().any().any():
            raise ValueError("dataset: missing values in required columns")

        if frame.duplicated(keys).any():
            raise ValueError("dataset: duplicate station-hour keys")

        times = frame["timestamp"]
        if (
            not isinstance(times.dtype, pd.DatetimeTZDtype)
            or str(times.dt.tz) != "UTC"
        ):
            raise ValueError("dataset: timestamps must be timezone-aware UTC")

        if not times.eq(times.dt.floor("h")).all():
            raise ValueError("dataset: timestamps must be hour starts")

        for column in self.target_columns:
            if (
                not pd.api.types.is_integer_dtype(frame[column].dtype)
                or frame[column].lt(0).any()
            ):
                raise ValueError(
                    "dataset: targets must be non-negative integers"
                )

        if not isinstance(self.dropped_rows, dict) or any(
            not isinstance(reason, str) or not reason
            or type(count) is not int or count < 0
            for reason, count in self.dropped_rows.items()
        ):
            raise ValueError(
                "dropped_rows must map reasons to non-negative integers"
            )