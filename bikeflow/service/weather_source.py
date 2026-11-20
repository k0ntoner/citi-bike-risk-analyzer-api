"""Historical weather prepared for the PROCESS input contract."""

from math import isfinite

import pandas as pd
import requests

from bikeflow.contracts.process import validate_weather

# Shared reference point for JC/HB weather.
# Use the same requested coordinates for training and prediction.
REGIONAL_WEATHER_LATITUDE = 40.73
REGIONAL_WEATHER_LONGITUDE = -74.04

_COLUMNS = {
    "temperature_2m": "temperature_c",
    "precipitation": "precipitation_mm",
    "wind_speed_10m": "wind_speed_ms",
}
_UNITS = {
    "time": "unixtime",
    "temperature_2m": "°C",
    "precipitation": "mm",
    "wind_speed_10m": "m/s",
}


class OpenMeteoArchiveSource:
    """Load a shared regional weather series for a requested UTC interval.

    Coordinates are supplied by the caller. A session can be injected in tests.
    HTTP errors propagate; missing weather values remain missing.
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        session: requests.Session | None = None,
        timeout: float = 60,
    ) -> None:
        for name, value, limit in (
            ("latitude", latitude, 90),
            ("longitude", longitude, 180),
        ):
            if type(value) not in (int, float) or not isfinite(value) or abs(value) > limit:
                raise ValueError(f"{name}: finite coordinate within ±{limit} required")
        if type(timeout) not in (int, float) or not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout: positive finite seconds required")
        self._latitude = latitude
        self._longitude = longitude
        self._get = requests.get if session is None else session.get
        self._timeout = timeout

    @property
    def latitude(self) -> float:
        """Requested regional latitude, recorded with the processed dataset."""
        return self._latitude

    @property
    def longitude(self) -> float:
        """Requested regional longitude, recorded with the processed dataset."""
        return self._longitude

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Return weather for [start, end), with UTC hour-aligned bounds.

        Include the API sample at end: its precipitation belongs to the
        last requested hour. Temperature and wind keep their original times.
        """
        for name, value in (("start", start), ("end", end)):
            if (
                not isinstance(value, pd.Timestamp)
                or pd.isna(value)
                or str(value.tz) != "UTC"
                or value != value.floor("h")
            ):
                raise ValueError(f"{name}: a UTC hour-start Timestamp is required")
        if start >= end:
            raise ValueError("start must be before end")

        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "hourly": ",".join(_COLUMNS),
            "timezone": "GMT",
            "timeformat": "unixtime",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "wind_speed_unit": "ms",
        }
        with self._get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=params,
            timeout=self._timeout,
        ) as response:
            response.raise_for_status()
            weather = _prepare_weather(response.json())

        return weather.loc[
            weather["timestamp"].ge(start) & weather["timestamp"].lt(end)
        ].sort_values("timestamp").reset_index(drop=True)


def _prepare_weather(payload: dict) -> pd.DataFrame:
    """Validate an API response and align precipitation by exact timestamps."""
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("weather response: expected a successful JSON object")
    hourly = payload.get("hourly")
    units = payload.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("weather response: hourly and hourly_units are required")
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("weather response: UTC data required")
    if any(units.get(name) != unit for name, unit in _UNITS.items()):
        raise ValueError("weather response: unexpected units")

    names = ["time", *_COLUMNS]
    if any(not isinstance(hourly.get(name), list) for name in names):
        raise ValueError("weather response: hourly arrays are required")
    if len({len(hourly[name]) for name in names}) != 1:
        raise ValueError("weather response: hourly arrays must have equal lengths")
    if any(type(value) is not int for value in hourly["time"]):
        raise ValueError("weather response: integer Unix seconds required")

    data = {"timestamp": pd.to_datetime(hourly["time"], unit="s", utc=True)}
    for source, target in _COLUMNS.items():
        values = hourly[source]
        if any(value is not None and type(value) not in (int, float) for value in values):
            raise ValueError(f"weather response: {source} must contain numbers or null")
        data[target] = pd.Series(values, dtype="float64")
    weather = validate_weather(pd.DataFrame(data))

    # The value labelled t + 1 h describes precipitation during [t, t + 1 h).
    # Reindex by time, not by row position: gaps must stay missing.
    precipitation = weather.set_index("timestamp")["precipitation_mm"]
    next_hour = weather["timestamp"] + pd.Timedelta(hours=1)
    weather["precipitation_mm"] = precipitation.reindex(next_hour).to_numpy()
    return weather