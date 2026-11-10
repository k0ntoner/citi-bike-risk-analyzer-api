
"""Contracts of the ANALYZE_RAW step."""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from bikeflow.contracts import raw_trips


@dataclass(frozen=True)
class AnalyzeRawInput:
    """Input of the ANALYZE_RAW step.

    Attributes:
        trips: Raw trips matching the raw_trips schema.
            The DataFrame is shared with PROCESS and must not be modified.
    """

    trips: pd.DataFrame

    def __post_init__(self) -> None:
        raw_trips.validate(self.trips)


@dataclass(frozen=True)
class GeneralStatistics:
    """General information about the raw trip dataset."""

    total_trips: int
    period_start: datetime | None
    period_end: datetime | None
    unique_stations: int


@dataclass(frozen=True)
class DataQuality:
    """Metrics describing missing and invalid data."""

    missing_values: dict[str, int]
    duplicate_ride_ids: int
    invalid_records: dict[str, int]


@dataclass(frozen=True)
class TripAnomalies:
    """Statistics about unusual trip durations and routes."""

    long_rides: int
    short_round_trips: int
    zero_duration: int
    negative_duration: int


@dataclass(frozen=True)
class StationActivity:
    """Activity statistics for one station."""

    station_id: str
    station_name: str | None
    departures: int
    arrivals: int


@dataclass(frozen=True)
class StationAnalysis:
    """Statistics describing station activity."""

    total_stations: int
    stations: list[StationActivity]
    stations_without_departures: list[str]
    stations_without_arrivals: list[str]


@dataclass(frozen=True)
class Distributions:
    """Distributions of trips across different categories and time periods."""

    by_bike_type: dict[str, int]
    by_user_type: dict[str, int]
    by_hour: dict[int, int]
    by_weekday: dict[int, int]
    by_month: dict[int, int]


@dataclass(frozen=True)
class AnalyzeRawResult:
    """Complete result of the ANALYZE_RAW step, divided into five groups."""

    general_statistics: GeneralStatistics
    data_quality: DataQuality
    trip_anomalies: TripAnomalies
    station_analysis: StationAnalysis
    distributions: Distributions
