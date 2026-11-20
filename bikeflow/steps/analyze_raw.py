
"""ANALYZE_RAW step: statistics of the raw trip dataset for the UI."""

import pandas as pd

from bikeflow.contracts.analyze_raw import (
    AnalyzeRawInput,
    AnalyzeRawResult,
    GeneralStatistics,
    DataQuality,
    TripAnomalies,
    StationActivity,
    StationAnalysis,
    Distributions
)
from bikeflow.contracts.raw_trips import RawTrip


class AnalyzeRawService:
    """Service responsible for analyzing raw trip data."""

    def analyze_raw(
        self,
        step_input: AnalyzeRawInput,
    ) -> AnalyzeRawResult:
        """Analyze raw trips and return all analysis results.

        The input DataFrame is shared with other pipeline steps
        and is not modified.
        """

        trips = step_input.trips

        return AnalyzeRawResult(
            general_statistics=_analyze_general_statistics(trips),
            data_quality=_analyze_data_quality(trips),
            trip_anomalies=_analyze_trip_anomalies(trips),
            station_analysis=_analyze_station_analysis(trips),
            distributions=_analyze_distributions(trips),
        )


def _analyze_general_statistics(
    trips: pd.DataFrame,
) -> GeneralStatistics:
    """Calculate general statistics of the raw trip dataset.

    The input DataFrame is not modified.
    """
    total_trips = len(trips)

    if trips.empty:
        period_start = None
        period_end = None
    else:
        period_start = trips[RawTrip.STARTED_AT].min().to_pydatetime()
        period_end = trips[RawTrip.ENDED_AT].max().to_pydatetime()

    station_ids = pd.concat(
        [
            trips[RawTrip.START_STATION_ID],
            trips[RawTrip.END_STATION_ID],
        ],
        ignore_index=True,
    )

    unique_stations = int(station_ids.nunique())

    return GeneralStatistics(
        total_trips=total_trips,
        period_start=period_start,
        period_end=period_end,
        unique_stations=unique_stations,
    )

def _analyze_data_quality(
    trips: pd.DataFrame,
) -> DataQuality:
    """Calculate data quality metrics without modifying the input."""

    # Count missing values in every column.
    missing_values = {
        column: int(count)
        for column, count in trips.isna().sum().items()
    }

    # Count repeated ride IDs (excluding the first occurrence).
    duplicate_ride_ids = int(
        trips[RawTrip.RIDE_ID].duplicated().sum()
    )

    # Check whether bike and user types belong to known categories.
    invalid_bike_type = int(
        (
            trips[RawTrip.RIDEABLE_TYPE].notna()
            & ~trips[RawTrip.RIDEABLE_TYPE].isin(
                ["classic_bike", "electric_bike"]
            )
        ).sum()
    )

    invalid_user_type = int(
        (
            trips[RawTrip.MEMBER_CASUAL].notna()
            & ~trips[RawTrip.MEMBER_CASUAL].isin(
                ["member", "casual"]
            )
        ).sum()
    )

    # Check coordinate values against global geographic limits.
    invalid_coordinates_mask = pd.Series(
        False,
        index=trips.index,
    )

    coordinate_limits = (
        (RawTrip.START_LAT, -90, 90),
        (RawTrip.END_LAT, -90, 90),
        (RawTrip.START_LNG, -180, 180),
        (RawTrip.END_LNG, -180, 180),
    )

    for column, minimum, maximum in coordinate_limits:
        invalid_coordinates_mask |= (
            trips[column].notna()
            & ~trips[column].between(minimum, maximum)
        )

    invalid_coordinates = int(invalid_coordinates_mask.sum())

    # Check chronological consistency.
    end_before_start = int(
        (trips[RawTrip.ENDED_AT] < trips[RawTrip.STARTED_AT]).sum()
    )

    invalid_records = {
        "invalid_bike_type": invalid_bike_type,
        "invalid_user_type": invalid_user_type,
        "invalid_coordinates": invalid_coordinates,
        "end_before_start": end_before_start,
    }

    return DataQuality(
        missing_values=missing_values,
        duplicate_ride_ids=duplicate_ride_ids,
        invalid_records=invalid_records,
    )


def _analyze_trip_anomalies(
    trips: pd.DataFrame,
) -> TripAnomalies:
    """Calculate trip duration and round-trip anomalies."""

    # Calculate duration in seconds without modifying the input.
    durations = (
        trips[RawTrip.ENDED_AT] - trips[RawTrip.STARTED_AT]
    ).dt.total_seconds()

    # Rides longer than 3 hours.
    long_rides = int((durations > 3 * 60 * 60).sum())

    # A round trip starts and ends at the same known station.
    start_stations = trips[RawTrip.START_STATION_ID]
    end_stations = trips[RawTrip.END_STATION_ID]

    same_station = (
        start_stations.notna()
        & end_stations.notna()
        & start_stations.eq(end_stations).fillna(False)
    )

    # Round trips lasting from 0 up to (but not including) 2 minutes.
    short_round_trips = int(
        (
            same_station
            & (durations >= 0)
            & (durations < 2 * 60)
        ).sum()
    )

    zero_duration = int((durations == 0).sum())
    negative_duration = int((durations < 0).sum())

    return TripAnomalies(
        long_rides=long_rides,
        short_round_trips=short_round_trips,
        zero_duration=zero_duration,
        negative_duration=negative_duration,
    )

def _analyze_station_analysis(
    trips: pd.DataFrame,
) -> StationAnalysis:
    """Calculate departure and arrival activity for each station.

    The input DataFrame is not modified.
    """

    departures = (
        trips[RawTrip.START_STATION_ID]
        .dropna()
        .value_counts()
    )

    arrivals = (
        trips[RawTrip.END_STATION_ID]
        .dropna()
        .value_counts()
    )

    station_ids = sorted(
        set(departures.index) | set(arrivals.index)
    )

    station_names = {}

    for station_id in station_ids:
        start_names = trips.loc[
            trips[RawTrip.START_STATION_ID] == station_id,
            RawTrip.START_STATION_NAME,
        ].dropna()

        end_names = trips.loc[
            trips[RawTrip.END_STATION_ID] == station_id,
            RawTrip.END_STATION_NAME,
        ].dropna()

        names = pd.concat(
            [start_names, end_names],
            ignore_index=True,
        )

        station_names[station_id] = (
            str(names.iloc[0]) if not names.empty else None
        )

    stations = [
        StationActivity(
            station_id=str(station_id),
            station_name=station_names[station_id],
            departures=int(departures.get(station_id, 0)),
            arrivals=int(arrivals.get(station_id, 0)),
        )
        for station_id in station_ids
    ]

    stations_without_departures = [
        str(station_id)
        for station_id in station_ids
        if departures.get(station_id, 0) == 0
    ]

    stations_without_arrivals = [
        str(station_id)
        for station_id in station_ids
        if arrivals.get(station_id, 0) == 0
    ]

    return StationAnalysis(
        total_stations=len(station_ids),
        stations=stations,
        stations_without_departures=stations_without_departures,
        stations_without_arrivals=stations_without_arrivals,
    )


def _analyze_distributions(
    trips: pd.DataFrame,
) -> Distributions:
    """Calculate distributions of trips by type and start time.

    The input DataFrame is not modified.
    """

    by_bike_type = {
        str(key): int(value)
        for key, value in trips[RawTrip.RIDEABLE_TYPE]
        .value_counts()
        .items()
    }

    by_user_type = {
        str(key): int(value)
        for key, value in trips[RawTrip.MEMBER_CASUAL]
        .value_counts()
        .items()
    }

    by_hour = {
        int(key): int(value)
        for key, value in trips[RawTrip.STARTED_AT]
        .dt.hour
        .value_counts()
        .sort_index()
        .items()
    }

    by_weekday = {
        int(key): int(value)
        for key, value in trips[RawTrip.STARTED_AT]
        .dt.dayofweek
        .value_counts()
        .sort_index()
        .items()
    }

    by_month = {
        int(key): int(value)
        for key, value in trips[RawTrip.STARTED_AT]
        .dt.month
        .value_counts()
        .sort_index()
        .items()
    }

    return Distributions(
        by_bike_type=by_bike_type,
        by_user_type=by_user_type,
        by_hour=by_hour,
        by_weekday=by_weekday,
        by_month=by_month,
    )

