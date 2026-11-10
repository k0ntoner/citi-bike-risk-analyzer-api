
import pandas as pd

from bikeflow.contracts.raw_trips import RawTrip
from bikeflow.steps.analyze_raw import _analyze_general_statistics
from bikeflow.steps.analyze_raw import _analyze_data_quality
from bikeflow.steps.analyze_raw import _analyze_trip_anomalies
from bikeflow.steps.analyze_raw import _analyze_station_analysis
from bikeflow.steps.analyze_raw import _analyze_distributions
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.contracts.analyze_raw import (
    AnalyzeRawInput,
    AnalyzeRawResult,
    GeneralStatistics,
    DataQuality,
    TripAnomalies,
    StationAnalysis,
    Distributions
)


def test_general_statistics():
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.to_datetime(
                ["2026-09-01 10:00:00", "2026-09-02 12:00:00"]
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                ["2026-09-01 10:30:00", "2026-09-02 13:00:00"]
            ),
            RawTrip.START_STATION_ID: pd.Series(
                ["A", "B"], dtype="string"
            ),
            RawTrip.END_STATION_ID: pd.Series(
                ["B", "C"], dtype="string"
            ),
        }
    )

    result = _analyze_general_statistics(trips)

    assert result.total_trips == 2
    assert result.period_start == pd.Timestamp("2026-09-01 10:00:00")
    assert result.period_end == pd.Timestamp("2026-09-02 13:00:00")
    assert result.unique_stations == 3


def test_general_statistics_empty_dataset():
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.Series(dtype="datetime64[ns]"),
            RawTrip.ENDED_AT: pd.Series(dtype="datetime64[ns]"),
            RawTrip.START_STATION_ID: pd.Series(dtype="string"),
            RawTrip.END_STATION_ID: pd.Series(dtype="string"),
        }
    )

    result = _analyze_general_statistics(trips)

    assert result.total_trips == 0
    assert result.period_start is None
    assert result.period_end is None
    assert result.unique_stations == 0


def test_data_quality():
    trips = pd.DataFrame(
        {
            RawTrip.RIDE_ID: pd.Series(
                ["A", "A", "C"], dtype="string"
            ),
            RawTrip.RIDEABLE_TYPE: pd.Series(
                ["classic_bike", "unknown_bike", "electric_bike"],
                dtype="string",
            ),
            RawTrip.STARTED_AT: pd.to_datetime(
                [
                    "2026-09-01 10:00:00",
                    "2026-09-01 12:00:00",
                    "2026-09-01 14:00:00",
                ]
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                [
                    "2026-09-01 11:00:00",
                    "2026-09-01 11:00:00",
                    "2026-09-01 15:00:00",
                ]
            ),
            RawTrip.MEMBER_CASUAL: pd.Series(
                ["member", "casual", "unknown"], dtype="string"
            ),
            RawTrip.START_STATION_ID: pd.Series(
                ["A", None, "B"], dtype="string"
            ),
            RawTrip.START_STATION_NAME: pd.Series(
                ["Station A", None, "Station B"], dtype="string"
            ),
            RawTrip.END_STATION_ID: pd.Series(
                ["B", "C", "D"], dtype="string"
            ),
            RawTrip.END_STATION_NAME: pd.Series(
                ["Station B", "Station C", "Station D"], dtype="string"
            ),
            RawTrip.START_LAT: [40.7, 95.0, 40.8],
            RawTrip.START_LNG: [-74.0, -74.0, -74.0],
            RawTrip.END_LAT: [40.8, 40.9, 40.9],
            RawTrip.END_LNG: [-73.9, -74.0, -200.0],
        }
    )

    result = _analyze_data_quality(trips)

    assert result.duplicate_ride_ids == 1

    assert result.missing_values[RawTrip.START_STATION_ID] == 1
    assert result.missing_values[RawTrip.START_STATION_NAME] == 1

    assert result.invalid_records == {
        "invalid_bike_type": 1,
        "invalid_user_type": 1,
        "invalid_coordinates": 2,
        "end_before_start": 1,
    }


def test_trip_anomalies():
    trips = pd.DataFrame(
        {
            RawTrip.STARTED_AT: pd.to_datetime(
                [
                    "2026-09-01 10:00:00",
                    "2026-09-01 11:00:00",
                    "2026-09-01 12:00:00",
                    "2026-09-01 13:00:00",
                    "2026-09-01 14:00:00",
                ]
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                [
                    "2026-09-01 13:30:00",  # 3.5 hours
                    "2026-09-01 11:01:00",  # 1 minute, same station
                    "2026-09-01 12:00:00",  # zero duration
                    "2026-09-01 12:59:00",  # negative duration
                    "2026-09-01 14:01:00",  # 1 minute, different stations
                ]
            ),
            RawTrip.START_STATION_ID: pd.Series(
                ["A", "B", "C", "D", "E"], dtype="string"
            ),
            RawTrip.END_STATION_ID: pd.Series(
                ["F", "B", "C", "D", "G"], dtype="string"
            ),
        }
    )

    result = _analyze_trip_anomalies(trips)

    assert result.long_rides == 1
    assert result.short_round_trips == 2
    assert result.zero_duration == 1
    assert result.negative_duration == 1

def test_station_analysis():
    trips = pd.DataFrame(
        {
            RawTrip.START_STATION_ID: pd.Series(
                ["A", "A", "B", "C"], dtype="string"
            ),
            RawTrip.END_STATION_ID: pd.Series(
                ["B", "C", "C", "D"], dtype="string"
            ),
            RawTrip.START_STATION_NAME: pd.Series(
                ["Station A", "Station A", "Station B", "Station C"],
                dtype="string",
            ),
            RawTrip.END_STATION_NAME: pd.Series(
                ["Station B", "Station C", "Station C", "Station D"],
                dtype="string",
            ),
        }
    )

    result = _analyze_station_analysis(trips)

    assert result.total_stations == 4
    assert result.stations_without_departures == ["D"]
    assert result.stations_without_arrivals == ["A"]

    station_a = next(
        station for station in result.stations
        if station.station_id == "A"
    )
    assert station_a.station_name == "Station A"
    assert station_a.departures == 2
    assert station_a.arrivals == 0

    station_c = next(
        station for station in result.stations
        if station.station_id == "C"
    )
    assert station_c.departures == 1
    assert station_c.arrivals == 2


def test_distributions():
    trips = pd.DataFrame(
        {
            RawTrip.RIDEABLE_TYPE: pd.Series(
                [
                    "classic_bike",
                    "electric_bike",
                    "classic_bike",
                    "classic_bike",
                ],
                dtype="string",
            ),
            RawTrip.MEMBER_CASUAL: pd.Series(
                ["member", "casual", "member", "casual"],
                dtype="string",
            ),
            RawTrip.STARTED_AT: pd.to_datetime(
                [
                    "2026-09-07 08:15:00",  # Monday
                    "2026-09-07 08:30:00",  # Monday
                    "2026-09-08 14:00:00",  # Tuesday
                    "2026-10-08 14:30:00",  # Thursday
                ]
            ),
        }
    )

    result = _analyze_distributions(trips)

    assert result.by_bike_type == {
        "classic_bike": 3,
        "electric_bike": 1,
    }

    assert result.by_user_type == {
        "member": 2,
        "casual": 2,
    }

    assert result.by_hour == {
        8: 2,
        14: 2,
    }

    assert result.by_weekday == {
        0: 2,
        1: 1,
        3: 1,
    }

    assert result.by_month == {
        9: 3,
        10: 1,
    }

def test_analyze_raw_service():
    """Test the complete ANALYZE_RAW service and input immutability."""

    trips = pd.DataFrame(
        {
            RawTrip.RIDE_ID: pd.Series(
                ["ride-001", "ride-002", "ride-003"],
                dtype="string",
            ),
            RawTrip.RIDEABLE_TYPE: pd.Series(
                ["classic_bike", "electric_bike", "classic_bike"],
                dtype="string",
            ),
            RawTrip.STARTED_AT: pd.to_datetime(
                [
                    "2026-09-07 08:00:00",
                    "2026-09-07 08:30:00",
                    "2026-09-08 14:00:00",
                ]
            ),
            RawTrip.ENDED_AT: pd.to_datetime(
                [
                    "2026-09-07 08:30:00",
                    "2026-09-07 12:30:00",
                    "2026-09-08 14:01:30",
                ]
            ),
            RawTrip.START_STATION_NAME: pd.Series(
                ["Station A", "Station B", "Station C"],
                dtype="string",
            ),
            RawTrip.START_STATION_ID: pd.Series(
                ["A", "B", "C"],
                dtype="string",
            ),
            RawTrip.END_STATION_NAME: pd.Series(
                ["Station B", "Station C", "Station C"],
                dtype="string",
            ),
            RawTrip.END_STATION_ID: pd.Series(
                ["B", "C", "C"],
                dtype="string",
            ),
            RawTrip.START_LAT: [40.700, 40.710, 40.720],
            RawTrip.START_LNG: [-74.000, -74.010, -74.020],
            RawTrip.END_LAT: [40.710, 40.720, 40.720],
            RawTrip.END_LNG: [-74.010, -74.020, -74.020],
            RawTrip.MEMBER_CASUAL: pd.Series(
                ["member", "casual", "member"],
                dtype="string",
            ),
        }
    )

    original_trips = trips.copy(deep=True)

    step_input = AnalyzeRawInput(trips=trips)

    service = AnalyzeRawService()
    result = service.analyze_raw(step_input)

    # Check the result structure.
    assert isinstance(result, AnalyzeRawResult)
    assert isinstance(result.general_statistics, GeneralStatistics)
    assert isinstance(result.data_quality, DataQuality)
    assert isinstance(result.trip_anomalies, TripAnomalies)
    assert isinstance(result.station_analysis, StationAnalysis)
    assert isinstance(result.distributions, Distributions)

    # 1. General statistics.
    assert result.general_statistics.total_trips == 3
    assert result.general_statistics.period_start == pd.Timestamp(
        "2026-09-07 08:00:00"
    )
    assert result.general_statistics.period_end == pd.Timestamp(
        "2026-09-08 14:01:30"
    )
    assert result.general_statistics.unique_stations == 3

    # 2. Data quality.
    assert result.data_quality.duplicate_ride_ids == 0
    assert all(
        count == 0
        for count in result.data_quality.missing_values.values()
    )
    assert all(
        count == 0
        for count in result.data_quality.invalid_records.values()
    )

    # 3. Trip anomalies.
    assert result.trip_anomalies.long_rides == 1
    assert result.trip_anomalies.short_round_trips == 1
    assert result.trip_anomalies.zero_duration == 0
    assert result.trip_anomalies.negative_duration == 0

    # 4. Station analysis.
    assert result.station_analysis.total_stations == 3
    assert result.station_analysis.stations_without_departures == []
    assert result.station_analysis.stations_without_arrivals == ["A"]

    stations = {
        station.station_id: station
        for station in result.station_analysis.stations
    }

    assert stations["A"].station_name == "Station A"
    assert stations["A"].departures == 1
    assert stations["A"].arrivals == 0

    assert stations["B"].departures == 1
    assert stations["B"].arrivals == 1

    assert stations["C"].departures == 1
    assert stations["C"].arrivals == 2

    # 5. Distributions.
    assert result.distributions.by_bike_type == {
        "classic_bike": 2,
        "electric_bike": 1,
    }
    assert result.distributions.by_user_type == {
        "member": 2,
        "casual": 1,
    }
    assert result.distributions.by_hour == {
        8: 2,
        14: 1,
    }
    assert result.distributions.by_weekday == {
        0: 2,
        1: 1,
    }
    assert result.distributions.by_month == {
        9: 3,
    }

    # The input DataFrame must remain unchanged.
    pd.testing.assert_frame_equal(trips, original_trips)
