"""Tests for the assumed activity schedule shared by all input stations."""

import pandas as pd
import pytest

from bikeflow.contracts.raw_trips import RawTrip
from bikeflow.steps.process import _assume_station_periods


def test_assume_station_periods():
    """Include both endpoints, earlier periods and gaps, preserving inputs."""
    trips = pd.DataFrame(
        [
            (" JC001 ", "HB001", "2026-01-01 00:10"),
            ("JC002", None, "2026-01-03 00:10"),
            ("JC001", "6578.01", "2026-01-03 00:20"),
            (None, " ", "2026-01-03 00:30"),
        ],
        columns=[
            RawTrip.START_STATION_ID,
            RawTrip.END_STATION_ID,
            RawTrip.STARTED_AT,
        ],
    )
    trips[RawTrip.STARTED_AT] = pd.to_datetime(trips[RawTrip.STARTED_AT])
    dates = pd.date_range("2026-01-01", periods=4, freq="D", tz="UTC")
    coverage = pd.DataFrame(
        [(dates[2], dates[3]), (dates[0], dates[1]), (dates[0], dates[1])],
        columns=["period_start", "period_end"],
    )
    original_trips = trips.copy(deep=True)
    original_coverage = coverage.copy(deep=True)

    result = _assume_station_periods(trips, coverage)

    assert result.columns.tolist() == ["station_id", "period_start", "period_end"]
    assert list(result.itertuples(index=False, name=None)) == [
        ("HB001", dates[0], dates[1]),
        ("HB001", dates[2], dates[3]),
        ("JC001", dates[0], dates[1]),
        ("JC001", dates[2], dates[3]),
        ("JC002", dates[0], dates[1]),
        ("JC002", dates[2], dates[3]),
    ]
    pd.testing.assert_frame_equal(trips, original_trips)
    pd.testing.assert_frame_equal(coverage, original_coverage)


def test_assume_station_periods_empty():
    """Return typed empty periods if stations or coverage are absent."""
    trips = pd.DataFrame(
        [("JC001", None)],
        columns=[RawTrip.START_STATION_ID, RawTrip.END_STATION_ID],
    )
    start = pd.Timestamp("2026-01-01", tz="UTC")
    coverage = pd.DataFrame(
        [(start, start + pd.Timedelta(days=1))],
        columns=["period_start", "period_end"],
    )
    outside = trips.copy()
    outside[RawTrip.START_STATION_ID] = "6578.01"

    for source, periods in (
        (trips.iloc[:0], coverage),
        (trips, coverage.iloc[:0]),
        (outside, coverage),
    ):
        result = _assume_station_periods(source, periods)
        assert result.empty
        assert result.columns.tolist() == ["station_id", "period_start", "period_end"]
        assert str(result["period_start"].dt.tz) == "UTC"
        assert str(result["period_end"].dt.tz) == "UTC"


def test_assume_station_periods_rejects_invalid_coverage():
    """Reject missing fields, naive or missing times and invalid bounds."""
    trips = pd.DataFrame(
        [("JC001", "HB001")],
        columns=[RawTrip.START_STATION_ID, RawTrip.END_STATION_ID],
    )
    start = pd.Timestamp("2026-01-01", tz="UTC")
    coverage = pd.DataFrame(
        [(start, start + pd.Timedelta(days=1))],
        columns=["period_start", "period_end"],
    )

    with pytest.raises(ValueError, match="missing columns"):
        _assume_station_periods(trips, coverage.drop(columns="period_end"))
    with pytest.raises(ValueError, match="missing columns"):
        _assume_station_periods(trips.drop(columns=RawTrip.END_STATION_ID), coverage)

    naive = coverage.copy()
    naive["period_start"] = naive["period_start"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        _assume_station_periods(trips, naive)

    missing = coverage.copy()
    missing.loc[0, "period_end"] = pd.NaT
    with pytest.raises(ValueError, match="UTC"):
        _assume_station_periods(trips, missing)

    for end in (start, start - pd.Timedelta(hours=1)):
        invalid = coverage.copy()
        invalid["period_end"] = end
        with pytest.raises(ValueError, match="must precede"):
            _assume_station_periods(trips, invalid)
