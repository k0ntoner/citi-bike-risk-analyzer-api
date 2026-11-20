"""Archive weather boundary, alignment and failure behaviour."""

from copy import deepcopy
from unittest.mock import MagicMock, Mock

import pandas as pd
import pytest
import requests

from bikeflow.contracts.process import validate_weather
from bikeflow.service.weather_source import OpenMeteoArchiveSource


@pytest.fixture
def weather_http():
    """Fake API response spanning midnight; no live HTTP calls."""
    times = pd.date_range("2026-01-01 22:00", periods=4, freq="h", tz="UTC")
    payload = {
        "utc_offset_seconds": 0,
        "hourly_units": {
            "time": "unixtime", "temperature_2m": "°C",
            "precipitation": "mm", "wind_speed_10m": "m/s",
        },
        "hourly": {
            "time": [int(t.timestamp()) for t in times],
            "temperature_2m": [1, 2, 3, 4],
            "precipitation": [9, 0.7, 0, 1.2],
            "wind_speed_10m": [2, 3, 4, 5],
        },
    }
    response = MagicMock(spec=requests.Response)
    response.__enter__.return_value = response
    response.json.return_value = payload
    session = Mock(spec=requests.Session)
    session.get.return_value = response
    source = OpenMeteoArchiveSource(40.73, -74.04, session=session)
    return source, session, response, payload


def test_fetch_weather(weather_http):
    """Clip the interval and use the endpoint sample for the last hour's rain."""
    source, session, response, payload = weather_http
    original = deepcopy(payload)
    start = pd.Timestamp("2026-01-01 23:00", tz="UTC")
    end = pd.Timestamp("2026-01-02 01:00", tz="UTC")

    result = source.fetch(start, end)

    assert result["timestamp"].tolist() == [start, end - pd.Timedelta(hours=1)]
    assert result["temperature_c"].tolist() == [2, 3]
    assert result["wind_speed_ms"].tolist() == [3, 4]
    assert result["precipitation_mm"].tolist() == [0, 1.2]
    validate_weather(result)
    assert payload == original
    response.raise_for_status.assert_called_once()
    session.get.assert_called_once_with(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": 40.73, "longitude": -74.04,
            "start_date": "2026-01-01", "end_date": "2026-01-02",
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "GMT", "timeformat": "unixtime",
            "temperature_unit": "celsius", "precipitation_unit": "mm",
            "wind_speed_unit": "ms",
        },
        timeout=60,
    )


def test_fetch_keeps_gaps_and_nulls(weather_http):
    """Missing hours must not borrow precipitation from a later row."""
    source, _, _, payload = weather_http
    payload["hourly"]["temperature_2m"][2] = None
    payload["hourly"]["wind_speed_10m"][2] = 0
    # Remove 23:00, then reverse the remaining records.
    for name, values in payload["hourly"].items():
        payload["hourly"][name] = [values[i] for i in (3, 2, 0)]

    result = source.fetch(
        pd.Timestamp("2026-01-01 22:00", tz="UTC"),
        pd.Timestamp("2026-01-02 01:00", tz="UTC"),
    )

    assert result["timestamp"].dt.hour.tolist() == [22, 0]
    assert pd.isna(result.loc[0, "precipitation_mm"])
    assert result.loc[1, "precipitation_mm"] == 1.2
    assert pd.isna(result.loc[1, "temperature_c"])
    assert result.loc[1, "wind_speed_ms"] == 0


def test_fetch_preserves_both_autumn_hours(weather_http):
    """The two local 01:00 hours remain separate UTC observations."""
    source, _, _, payload = weather_http
    times = pd.date_range("2025-11-02 04:00", periods=4, freq="h", tz="UTC")
    payload["hourly"]["time"] = [int(t.timestamp()) for t in times]

    result = source.fetch(times[1], times[3])

    assert result["timestamp"].tolist() == [times[1], times[2]]
    local = result["timestamp"].dt.tz_convert("America/New_York")
    assert local.dt.hour.tolist() == [1, 1]
    assert result["precipitation_mm"].tolist() == [0, 1.2]


@pytest.mark.parametrize("empty", [False, True])
def test_fetch_missing_weather(weather_http, empty):
    """Null values and empty arrays remain valid missing data."""
    source, _, _, payload = weather_http
    for name in payload["hourly"]:
        if empty:
            payload["hourly"][name] = []
        elif name != "time":
            payload["hourly"][name] = [None] * 4

    result = source.fetch(
        pd.Timestamp("2026-01-01 23:00", tz="UTC"),
        pd.Timestamp("2026-01-02 01:00", tz="UTC"),
    )

    validate_weather(result)
    assert len(result) == (0 if empty else 2)
    assert result.drop(columns="timestamp").isna().all().all()


@pytest.mark.parametrize("section, name, value, message", [
    ("hourly_units", "wind_speed_10m", "km/h", "units"),
    ("hourly", "temperature_2m", [1, 2, 3], "equal lengths"),
    ("hourly", "wind_speed_10m", [1, 2, -1, 0], "non-negative"),
    ("hourly", "temperature_2m", [1, 2, float("inf"), 0], "finite"),
    ("hourly", "temperature_2m", [True, 2, 3, 4], "numbers or null"),
    ("hourly", "time", [0, 0, 3600, 7200], "duplicate"),
    ("hourly", "time", [0, 3600, 7201, 10800], "hour starts"),
    ("hourly", "time", [0, 3600, None, 10800], "integer Unix"),
])
def test_fetch_rejects_bad_weather(weather_http, section, name, value, message):
    """Malformed source data is rejected before it can enter PROCESS."""
    source, _, _, payload = weather_http
    payload[section][name] = value
    with pytest.raises(ValueError, match=message):
        source.fetch(
            pd.Timestamp("2026-01-01 23:00", tz="UTC"),
            pd.Timestamp("2026-01-02 01:00", tz="UTC"),
        )


@pytest.mark.parametrize("start, end", [
    ("2026-01-01", "2026-01-02T00:00Z"),
    ("2026-01-01T00:30Z", "2026-01-02T00:00Z"),
    ("2026-01-02T00:00Z", "2026-01-02T00:00Z"),
    ("2026-01-03T00:00Z", "2026-01-02T00:00Z"),
])
def test_fetch_rejects_bad_bounds(weather_http, start, end):
    """Reject invalid intervals without making an HTTP request."""
    source, session, _, _ = weather_http
    with pytest.raises(ValueError):
        source.fetch(pd.Timestamp(start), pd.Timestamp(end))
    session.get.assert_not_called()


@pytest.mark.parametrize("error", [requests.Timeout, requests.HTTPError])
def test_fetch_propagates_http_errors(weather_http, error):
    """A failed request is never converted into an empty weather table."""
    source, session, response, _ = weather_http
    failing_call = session.get if error is requests.Timeout else response.raise_for_status
    failing_call.side_effect = error("weather unavailable")
    with pytest.raises(error):
        source.fetch(
            pd.Timestamp("2026-01-01 23:00", tz="UTC"),
            pd.Timestamp("2026-01-02 01:00", tz="UTC"),
        )
    response.json.assert_not_called()