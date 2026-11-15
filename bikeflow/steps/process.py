"""PROCESS step: cleaning and aggregation into the training dataset."""

#from bikeflow.contracts.process import ProcessInput
import pandas as pd
import logging
from datetime import date
from holidays import country_holidays
from bikeflow.contracts.process import ProcessInput, ProcessResult
from bikeflow.contracts.raw_trips import RawTrip
from bikeflow.contracts.process import (
    WEATHER_FEATURE_COLUMNS,
    ProcessInput,
    ProcessResult,
    validate_weather,
)
log = logging.getLogger(__name__)
class ProcessService:
    """Turns raw trips into the dataset the model trains on.

    Works on the raw trips independently of the analysis steps and applies its
    own validation, including checks for problems absent from the current data
    (duplicates, end before start, DST). Aggregates trips into hourly
    per-station departures and arrivals, including zero hours, and joins the
    features (time, station, weather). Returns the dataset with a manifest of
    its feature and target columns.
    """

    def process(self, step_input: ProcessInput) -> ProcessResult:
        """Build the training table and its manifest without modifying inputs."""
        deduplicated, conflicting, duplicate_counts = _deduplicate_trips(
            step_input.trips,
        )
        network, network_counts = _filter_network_trips(deduplicated)
        cleaned, invalid_times, time_counts = _prepare_trip_times(network)

        # Проблемні години визначаємо за початковими часами записів.
        excluded_hours = pd.concat(
            [
                _build_excluded_hours(conflicting),
                _build_excluded_hours(invalid_times),
            ],
            ignore_index=True,
        ).drop_duplicates(["station_id", "timestamp"])

        source_periods = _build_source_periods(step_input.loaded_months)
        coverage_periods = _build_coverage_periods(
            source_periods,
            cleaned,
            departure_buffer=pd.Timedelta(
                hours=step_input.departure_buffer_hours,
            ),
        )

        # Перелік станцій беремо з початкового датасету.
        station_periods = _assume_station_periods(
            step_input.trips,
            coverage_periods,
        )
        eligible_hours = _build_eligible_hours(
            station_periods,
            excluded_hours,
        )

        # Агрегуємо всі очищені поїздки, потім застосовуємо погодинну сітку.
        counts = _aggregate_hourly_trips(cleaned)
        hourly = _complete_hourly_grid(counts, eligible_hours)

        hourly, weather_excluded = _join_weather(
            hourly,
            step_input.weather,
        )
        dataset = _add_holiday_feature(
            _add_time_features(hourly),
        )

        return ProcessResult(
            dataset=dataset,
            feature_columns=(
                "station_id",
                "hour",
                "weekday",
                "month",
                "is_weekend",
                "is_holiday",
                *WEATHER_FEATURE_COLUMNS,
            ),
            dropped_rows={
                **duplicate_counts,
                **network_counts,
                **time_counts,
            },
            loaded_months=step_input.loaded_months,
            departure_buffer_hours=step_input.departure_buffer_hours,
            weather_excluded_station_hours=weather_excluded,
        )

def _filter_network_trips(
    trips: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Keep trips touching JC/HB and mask endpoints outside that network.

    A cross-network trip still contributes its known JC/HB event.
    Entire trips are removed only when neither endpoint is in the network.
    Removal counts are disjoint. The input is not modified.
    """
    start = (
        trips[RawTrip.START_STATION_ID]
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )
    end = (
        trips[RawTrip.END_STATION_ID]
        .astype("string")
        .str.strip()
        .replace("", pd.NA)
    )

    start_in = start.str.startswith(("JC", "HB"), na=False)
    end_in = end.str.startswith(("JC", "HB"), na=False)

    keep = start_in | end_in
    missing_both = start.isna() & end.isna()
    outside = ~keep & ~missing_both

    filtered = trips.loc[keep].copy()
    filtered[RawTrip.START_STATION_ID] = start.loc[keep].where(
        start_in.loc[keep],
    )
    filtered[RawTrip.END_STATION_ID] = end.loc[keep].where(
        end_in.loc[keep],
    )

    return filtered, {
        "outside_network": int(outside.sum()),
        "missing_both_stations": int(missing_both.sum()),
    }

def _deduplicate_trips(
    trips: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Keep consistent trips and retain conflicting rows for coverage checks.

    Return cleaned trips, rejected conflicting rows, and removal counts.
    Exact duplicates of consistent trips are counted but not returned as
    rejected: one copy remains in the cleaned data.
    Conflicting rows retain all original values, including naive timestamps.
    Counts refer to removed rows, not unique IDs, and do not overlap.
    The input DataFrame is not modified.
    """
    distinct_rows = trips.drop_duplicates()

    conflicting_ids = distinct_rows.loc[
        distinct_rows[RawTrip.RIDE_ID].duplicated(keep=False),
        RawTrip.RIDE_ID,
    ]

    # Зберігаємо всі оригінальні записи з конфліктними ID.
    conflicting = trips[RawTrip.RIDE_ID].isin(conflicting_ids)
    rejected = trips.loc[conflicting].copy()

    # Для решти поїздок залишаємо один примірник.
    consistent = trips.loc[~conflicting]
    cleaned = consistent.drop_duplicates(
        subset=[RawTrip.RIDE_ID],
    ).copy()

    dropped = {
        "conflicting_ride_id": len(rejected),
        "duplicate_rows": len(consistent) - len(cleaned),
    }

    return cleaned, rejected, dropped

def _prepare_trip_times(
    trips: pd.DataFrame,
    timezone: str = "America/New_York",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Convert local trip times to UTC and separate invalid trips.

    Input timestamps are timezone-naive, as required by ProcessInput.
    Return valid trips, rejected trips with original times, and removal counts.
    Rejected trips must inform coverage checks before zero hours are added.
    """
    started = trips[RawTrip.STARTED_AT].dt.tz_localize(
        timezone,
        ambiguous="NaT",
        nonexistent="NaT",
    ).dt.tz_convert("UTC")

    ended = trips[RawTrip.ENDED_AT].dt.tz_localize(
        timezone,
        ambiguous="NaT",
        nonexistent="NaT",
    ).dt.tz_convert("UTC")

    # Час відсутній або його неможливо однозначно визначити.
    invalid_local_time = started.isna() | ended.isna()

    # Тривалість перевіряємо лише після врахування часового поясу.
    non_positive_duration = ~invalid_local_time & (ended <= started)

    keep = ~(invalid_local_time | non_positive_duration)

    cleaned = trips.loc[keep].copy()
    cleaned[RawTrip.STARTED_AT] = started.loc[keep]
    cleaned[RawTrip.ENDED_AT] = ended.loc[keep]

    # Зберігаємо проблемні записи з початковими значеннями часу.
    rejected = trips.loc[~keep].copy()

    dropped = {
        "invalid_local_time": int(invalid_local_time.sum()),
        "non_positive_duration": int(non_positive_duration.sum()),
    }

    return cleaned, rejected, dropped

def _aggregate_hourly_trips(trips: pd.DataFrame) -> pd.DataFrame:
    """Count departures and arrivals for each observed station-hour.

    Input trips have already been deduplicated, filtered and time-validated.
    Timestamps must be timezone-aware. The output uses UTC hour starts.
    Only station-hours with at least one event are returned.
    """
    counts = []

    event_columns = (
        (RawTrip.START_STATION_ID, RawTrip.STARTED_AT, "departures"),
        (RawTrip.END_STATION_ID, RawTrip.ENDED_AT, "arrivals"),
    )

    for station_column, time_column, target in event_columns:
        # Окрема таблиця подій: станція та час.
        events = (
            trips[[station_column, time_column]]
            .rename(
                columns={
                    station_column: "station_id",
                    time_column: "timestamp",
                },
            )
            .dropna(subset=["station_id"])
            .copy()
        )

        # Наприклад, 13:50 UTC належить інтервалу 13:00–14:00 UTC.
        events["timestamp"] = (
            events["timestamp"].dt.tz_convert("UTC").dt.floor("h")
        )

        hourly_counts = (
            events.groupby(
                ["station_id", "timestamp"],
                observed=True,
            )
            .size()
            .rename(target)
        )
        counts.append(hourly_counts)

    # Об'єднуємо виїзди й прибуття за однаковими станцією та годиною.
    hourly = (
        pd.concat(counts, axis=1)
        .fillna(0)
        .astype("int64")
        .reset_index()
    )

    return hourly.sort_values(
        ["station_id", "timestamp"],
        ignore_index=True,
    )

def _complete_hourly_grid(
    hourly: pd.DataFrame,
    eligible_hours: pd.DataFrame,
) -> pd.DataFrame:
    """Fill zero counts only for explicitly eligible station-hours.

    eligible_hours contains station_id and timestamp (UTC hour starts).
    Each row is accepted under the chosen activity and coverage assumptions.
    Exclude incomplete hours before calling, including rejected-trip effects.
    Events outside the supplied grid are omitted from the result.
    Neither input is modified.
    """
    keys = ["station_id", "timestamp"]
    targets = ["departures", "arrivals"]

    # Перевіряємо структуру та унікальність ключів.
    for name, frame, required in (
        ("hourly", hourly, keys + targets),
        ("eligible_hours", eligible_hours, keys),
    ):
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: missing columns {sorted(missing)}")

        if frame[keys].isna().any().any():
            raise ValueError(f"{name}: station-hour keys must not be missing")

        if frame.duplicated(keys).any():
            raise ValueError(f"{name}: duplicate station-hour keys")

        times = frame["timestamp"]
        if (
            not isinstance(times.dtype, pd.DatetimeTZDtype)
            or str(times.dt.tz) != "UTC"
        ):
            raise ValueError(f"{name}: timestamps must be timezone-aware UTC")

        if not times.eq(times.dt.floor("h")).all():
            raise ValueError(f"{name}: timestamps must be hour starts")

    # Пропуски у вже порахованих значеннях — помилка.
    counts = hourly[targets]
    if counts.isna().any().any() or counts.lt(0).any().any():
        raise ValueError("hourly: counts must be present and non-negative")

    for column in targets:
        if not pd.api.types.is_integer_dtype(hourly[column].dtype):
            raise ValueError("hourly: counts must have integer dtypes")

    # Беремо лише дозволені станції та години.
    result = eligible_hours[keys].merge(
        hourly[keys + targets],
        on=keys,
        how="left",
        validate="one_to_one",
    )

    # Нові пропуски означають відсутність подій у дозволену годину.
    result[targets] = result[targets].fillna(0).astype("int64")

    return result.sort_values(keys, ignore_index=True)

def _build_eligible_hours(
    station_periods: pd.DataFrame,
    excluded_hours: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build full UTC hours inside supplied station and coverage periods.

    station_periods: station_id, period_start, period_end (exclusive).
    Both boundaries must be timezone-aware UTC.
    Several periods per station are allowed.
    excluded_hours optionally removes known incomplete hours.
    """
    keys = ["station_id", "timestamp"]
    required = ["station_id", "period_start", "period_end"]

    missing = set(required) - set(station_periods.columns)
    if missing:
        raise ValueError(f"station_periods: missing columns {sorted(missing)}")

    periods = station_periods[required].copy()
    periods["station_id"] = periods["station_id"].astype("string").str.strip()

    if not periods["station_id"].str.startswith(("JC", "HB"), na=False).all():
        raise ValueError("station_periods: only JC/HB station IDs are allowed")

    for column in ("period_start", "period_end"):
        values = periods[column]
        if (
            not isinstance(values.dtype, pd.DatetimeTZDtype)
            or str(values.dt.tz) != "UTC"
            or values.isna().any()
        ):
            raise ValueError(f"{column}: non-missing UTC timestamps required")

    if (periods["period_start"] >= periods["period_end"]).any():
        raise ValueError("station_periods: period_start must precede period_end")

    parts = []

    for row in periods.itertuples(index=False):
        # Залишаємо лише години, які повністю входять в інтервал.
        first = row.period_start.ceil("h")
        stop = row.period_end.floor("h")

        if first < stop:
            hours = pd.date_range(
                first, stop, freq="h", inclusive="left",
            )
            parts.append(
                pd.DataFrame(
                    {"station_id": row.station_id, "timestamp": hours},
                ),
            )

    if parts:
        grid = pd.concat(parts, ignore_index=True)
    else:
        grid = pd.DataFrame(
            {
                "station_id": pd.Series(dtype="string"),
                "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            },
        )

    # Інтервали однієї станції можуть перетинатися.
    grid = grid.drop_duplicates(keys)

    if excluded_hours is not None:
        missing = set(keys) - set(excluded_hours.columns)
        if missing:
            raise ValueError(f"excluded_hours: missing columns {sorted(missing)}")

        if excluded_hours[keys].isna().any().any():
            raise ValueError("excluded_hours: station-hour keys must not be missing")

        times = excluded_hours["timestamp"]
        if (
            not isinstance(times.dtype, pd.DatetimeTZDtype)
            or str(times.dt.tz) != "UTC"
        ):
            raise ValueError("excluded_hours: timestamps must be UTC")

        if not times.eq(times.dt.floor("h")).all():
            raise ValueError("excluded_hours: timestamps must be hour starts")

        # Виключаємо конкретні пари «станція + година».
        grid_keys = pd.MultiIndex.from_frame(grid[keys])
        excluded_keys = pd.MultiIndex.from_frame(excluded_hours[keys])
        grid = grid.loc[~grid_keys.isin(excluded_keys)]

    return grid.sort_values(keys, ignore_index=True)


def _assume_station_periods(
    trips: pd.DataFrame,
    coverage_periods: pd.DataFrame,
) -> pd.DataFrame:
    """Apply shared data coverage to every JC/HB station in the raw input.

    All observed stations are assumed active throughout the study window.
    coverage_periods supplies UTC [period_start, period_end) intervals accepted
    under the chosen coverage policy; this function does not establish
    completeness. Missing intervals are not filled. Known bad hours
    are removed later by _build_eligible_hours through excluded_hours.
    Neither input is modified.
    """
    station_columns = [RawTrip.START_STATION_ID, RawTrip.END_STATION_ID]
    bounds = ["period_start", "period_end"]

    for name, frame, required in (
        ("trips", trips, station_columns),
        ("coverage_periods", coverage_periods, bounds),
    ):
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: missing columns {sorted(missing)}")

    periods = coverage_periods[bounds].copy()

    for column in bounds:
        values = periods[column]
        if (
            not isinstance(values.dtype, pd.DatetimeTZDtype)
            or str(values.dt.tz) != "UTC"
            or values.isna().any()
        ):
            raise ValueError(f"{column}: non-missing UTC timestamps required")

    if (periods["period_start"] >= periods["period_end"]).any():
        raise ValueError("coverage_periods: period_start must precede period_end")

    station_ids = (
        pd.concat([trips[column] for column in station_columns], ignore_index=True)
        .astype("string")
        .str.strip()
    )
    stations = (
        station_ids.loc[station_ids.str.startswith(("JC", "HB"), na=False)]
        .drop_duplicates()
        .to_frame(name="station_id")
    )

    # Every station gets every supplied interval, including periods before
    # its first recorded trip. Gaps between intervals stay uncovered.
    result = stations.merge(periods.drop_duplicates(), how="cross")

    return result.sort_values(
        ["station_id", "period_start", "period_end"],
        ignore_index=True,
    )

def _build_source_periods(
    loaded_months: tuple[date, ...],
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Build UTC intervals for successfully loaded local-calendar months.

    Each month is represented by its first day, as in IngestedFile.month.
    Adjacent months are merged; missing months remain gaps.
    Bounds are inclusive at the start and exclusive at the end.

    This records source availability, not final station-hour eligibility.
    Files are partitioned by ended_at, so departure completeness and effects
    of rejected records must be handled separately.
    """
    for month in loaded_months:
        if type(month) is not date or month.day != 1:
            raise ValueError(
                "loaded_months: use datetime.date values for the first day of each month"
            )

    periods: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    for month in sorted(set(loaded_months)):
        local_start = pd.Timestamp(month)
        local_end = local_start + pd.DateOffset(months=1)

        # Межі місяця — місцеві опівночі; зміщення від UTC може відрізнятися.
        start = local_start.tz_localize(timezone).tz_convert("UTC")
        end = local_end.tz_localize(timezone).tz_convert("UTC")

        if periods and periods[-1][1] == start:
            # Приєднуємо наступний місяць до безперервного інтервалу.
            periods[-1] = (periods[-1][0], end)
        else:
            periods.append((start, end))

    # Явні типи потрібні також для порожнього результату.
    return pd.DataFrame(
        {
            "period_start": pd.Series(
                [start for start, _ in periods],
                dtype="datetime64[ns, UTC]",
            ),
            "period_end": pd.Series(
                [end for _, end in periods],
                dtype="datetime64[ns, UTC]",
            ),
        },
    )

def _build_excluded_hours(
    rejected: pd.DataFrame,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Mark recorded station-hours affected by rejected trips.

    Input times are the original timezone-naive values. Ambiguous times mark
    both UTC possibilities. A nonexistent local time marks its station's
    entire local day as a conservative quality rule, not a time correction.
    Missing or non-network station IDs have no station-specific exclusion.
    A missing time at a known network station raises instead of guessing.
    The input is not modified.
    """
    event_columns = (
        (RawTrip.START_STATION_ID, RawTrip.STARTED_AT),
        (RawTrip.END_STATION_ID, RawTrip.ENDED_AT),
    )
    required = {column for pair in event_columns for column in pair}
    missing = required - set(rejected.columns)
    if missing:
        raise ValueError(f"rejected: missing columns {sorted(missing)}")

    for _, time_column in event_columns:
        if not pd.api.types.is_datetime64_dtype(rejected[time_column].dtype):
            raise ValueError("rejected: timezone-naive datetime columns required")

    # Збираємо виїзди та прибуття в одну таблицю подій.
    events = pd.concat(
        [
            rejected[[station_column, time_column]].rename(
                columns={station_column: "station_id", time_column: "local_time"},
            )
            for station_column, time_column in event_columns
        ],
        ignore_index=True,
    )
    events["station_id"] = events["station_id"].astype("string").str.strip()
    events = events.loc[
        events["station_id"].str.startswith(("JC", "HB"), na=False)
    ].copy()

    if events["local_time"].isna().any():
        raise ValueError("rejected: missing local time for a known station")

    parts = []

    # Для неоднозначного часу враховуємо обидві можливості.
    # Для звичайного часу вони збігаються; повтори приберемо наприкінці.
    for is_dst in (True, False):
        utc = events["local_time"].dt.tz_localize(
            timezone, ambiguous=is_dst, nonexistent="NaT",
        ).dt.tz_convert("UTC")

        hours = events[["station_id"]].copy()
        hours["timestamp"] = utc.dt.floor("h")
        parts.append(hours.dropna(subset=["timestamp"]))

    # Початкові NaT вже перевірено. Тут залишився лише неіснуючий час.
    invalid_days = events.loc[utc.isna()].copy()
    invalid_days["local_time"] = invalid_days["local_time"].dt.normalize()

    for station_id, day in invalid_days.drop_duplicates().itertuples(
        index=False, name=None,
    ):
        start = day.tz_localize(timezone).tz_convert("UTC")
        stop = (day + pd.DateOffset(days=1)).tz_localize(timezone).tz_convert("UTC")

        parts.append(
            pd.DataFrame(
                {
                    "station_id": station_id,
                    "timestamp": pd.date_range(
                        start, stop, freq="h", inclusive="left",
                    ),
                },
            ),
        )

    keys = ["station_id", "timestamp"]
    return (
        pd.concat(parts, ignore_index=True)
        .drop_duplicates(keys)
        .sort_values(keys, ignore_index=True)
    )

def _build_coverage_periods(
    source_periods: pd.DataFrame,
    trips: pd.DataFrame,
    departure_buffer: pd.Timedelta = pd.Timedelta(hours=48),
) -> pd.DataFrame:
    """Build approximate coverage by trimming the end of each source block.

    Source intervals must already be merged, as by _build_source_periods.
    Trips must have valid positive durations and UTC timestamps.
    The buffer is an explicit assumption about unseen trip completions, not
    proof of completeness. Observed durations exceeding it log a warning;
    processing continues with the configured buffer and all valid trips.
    No trips are removed or modified. Both targets share the retained periods;
    known bad station-hours must still be excluded separately.
    """
    if (
        not isinstance(departure_buffer, pd.Timedelta)
        or pd.isna(departure_buffer)
        or departure_buffer <= pd.Timedelta(0)
    ):
        raise ValueError("departure_buffer must be a positive pandas Timedelta")

    bounds = ["period_start", "period_end"]
    time_columns = [RawTrip.STARTED_AT, RawTrip.ENDED_AT]

    for name, frame, required in (
        ("source_periods", source_periods, bounds),
        ("trips", trips, time_columns),
    ):
        missing = set(required) - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: missing columns {sorted(missing)}")

        for column in required:
            values = frame[column]
            if (
                not isinstance(values.dtype, pd.DatetimeTZDtype)
                or str(values.dt.tz) != "UTC"
                or values.isna().any()
            ):
                raise ValueError(
                    f"{name}.{column}: non-missing UTC timestamps required"
                )

    durations = trips[RawTrip.ENDED_AT] - trips[RawTrip.STARTED_AT]

    if durations.le(pd.Timedelta(0)).any():
        raise ValueError("trips: positive durations required")

    long_trip_count = int(durations.gt(departure_buffer).sum())
    if long_trip_count:
        log.warning(
            "PROCESS: %d trip(s) exceed departure_buffer=%s; maximum duration=%s. "
            "Continuing with all valid trips and the configured buffer.",
            long_trip_count,
            departure_buffer,
            durations.max(),
        )

    periods = source_periods[bounds].sort_values("period_start").copy()

    if periods["period_start"].ge(periods["period_end"]).any():
        raise ValueError("source_periods: period_start must precede period_end")

    if periods["period_start"].le(periods["period_end"].shift()).any():
        raise ValueError(
            "source_periods: merge overlapping or adjacent intervals first"
        )

    # Відступаємо від кінця кожного безперервного блоку.
    periods["period_end"] = periods["period_end"] - departure_buffer

    # Короткий блок може повністю потрапити в буфер.
    return periods.loc[
        periods["period_start"] < periods["period_end"]
    ].reset_index(drop=True)

def _add_time_features(
    hourly: pd.DataFrame,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Add recurring local-calendar features while preserving UTC row keys.

    weekday is Monday=0 through Sunday=6. No year or exact-date features are
    added. The input must have non-missing UTC hour starts in timestamp.
    All original columns, row order and index are preserved in a new frame.
    """
    if "timestamp" not in hourly.columns:
        raise ValueError("hourly: missing timestamp column")

    times = hourly["timestamp"]
    if (
        not isinstance(times.dtype, pd.DatetimeTZDtype)
        or str(times.dt.tz) != "UTC"
        or times.isna().any()
    ):
        raise ValueError("hourly: non-missing UTC timestamps required")

    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("hourly: timestamps must be hour starts")

    feature_columns = {"hour", "weekday", "month", "is_weekend"}
    if feature_columns & set(hourly.columns):
        raise ValueError("hourly: time feature columns already present")

    local_time = times.dt.tz_convert(timezone)

    result = hourly.copy()
    result["hour"] = local_time.dt.hour.astype("int8")
    result["weekday"] = local_time.dt.day_of_week.astype("int8")
    result["month"] = local_time.dt.month.astype("int8")
    result["is_weekend"] = result["weekday"].ge(5)

    return result

def _add_holiday_feature(
    hourly: pd.DataFrame,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    """Mark US federal holiday dates and their observed substitute dates.

    Dates are evaluated in the local timezone; UTC row keys are preserved.
    No state-specific calendar is selected. The input is not modified.
    """
    if "timestamp" not in hourly.columns:
        raise ValueError("hourly: missing timestamp column")

    times = hourly["timestamp"]
    if (
        not isinstance(times.dtype, pd.DatetimeTZDtype)
        or str(times.dt.tz) != "UTC"
        or times.isna().any()
    ):
        raise ValueError("hourly: non-missing UTC timestamps required")

    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("hourly: timestamps must be hour starts")

    if "is_holiday" in hourly.columns:
        raise ValueError("hourly: is_holiday column already present")

    # Отримуємо місцеві календарні дати для порівняння зі святами.
    local_dates = (
        times.dt.tz_convert(timezone)
        .dt.tz_localize(None)
        .dt.normalize()
    )

    result = hourly.copy()
    result["is_holiday"] = False

    if result.empty:
        return result

    # Перенесений вихідний для Нового року може припасти на попередній рік.
    years = {
        int(year) + offset
        for year in local_dates.dt.year.unique()
        for offset in (-1, 0, 1)
    }

    calendar = country_holidays(
        "US",
        years=sorted(years),
        observed=True,
        expand=False,
    )

    holiday_dates = pd.to_datetime(list(calendar))
    result["is_holiday"] = local_dates.isin(holiday_dates)

    return result

def _join_weather(
    hourly: pd.DataFrame,
    weather: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Attach prepared weather and exclude station-hours with missing features.

    Return the usable table and the number of excluded station-hour rows,
    not trip rows. Existing values and row order are preserved for retained
    rows; the output index is reset. Neither input is modified.
    """
    validate_weather(weather)

    keys = ["station_id", "timestamp"]
    missing = set(keys) - set(hourly.columns)
    if missing:
        raise ValueError(f"hourly: missing columns {sorted(missing)}")

    if hourly[keys].isna().any().any():
        raise ValueError("hourly: missing station-hour keys")

    if hourly.duplicated(keys).any():
        raise ValueError("hourly: duplicate station-hour keys")

    times = hourly["timestamp"]
    if (
        not isinstance(times.dtype, pd.DatetimeTZDtype)
        or str(times.dt.tz) != "UTC"
    ):
        raise ValueError("hourly: timestamps must be UTC")

    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("hourly: timestamps must be hour starts")

    if set(WEATHER_FEATURE_COLUMNS) & set(hourly.columns):
        raise ValueError("hourly: weather feature columns already present")

    joined = hourly.merge(
        weather[["timestamp", *WEATHER_FEATURE_COLUMNS]],
        on="timestamp",
        how="left",
        sort=False,
        validate="many_to_one",
    )

    usable = joined[list(WEATHER_FEATURE_COLUMNS)].notna().all(axis=1)
    excluded_count = int((~usable).sum())

    return joined.loc[usable].reset_index(drop=True), excluded_count