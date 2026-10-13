import io

import pandas as pd
import pytest

from bikeflow.contracts.raw_trips import COLUMN_NAMES, RawTrip, SchemaError, read_raw_trips_csv, validate

CSV = """\
"ride_id","rideable_type","started_at","ended_at","start_station_name","start_station_id","end_station_name","end_station_id","start_lat","start_lng","end_lat","end_lng","member_casual"
"EFC244DEC104C0D9","electric_bike","2026-09-08 08:11:36.020","2026-09-08 08:28:50.409","Grand St","JC102","South End Ave & Albany St","5114.08",40.715,-74.037,40.709,-74.016,"member"
"C4500AE7EADAB74D","classic_bike","2026-09-18 19:29:50.583","2026-09-18 19:37:53.218","Morris Canal","JC072",,,40.712,-74.038,,,"casual"
"""


def read() -> pd.DataFrame:
    return read_raw_trips_csv(io.StringIO(CSV))


def test_read_csv_matches_schema():
    df = read()
    assert tuple(df.columns) == COLUMN_NAMES
    assert df[RawTrip.END_STATION_ID].iloc[0] == "5114.08"  # stays a string, not a float
    assert df[RawTrip.STARTED_AT].iloc[0] == pd.Timestamp("2026-09-08 08:11:36.020")
    assert df[RawTrip.END_STATION_ID].isna().iloc[1]  # nullable column may be empty


def test_validate_reports_all_problems():
    df = read().drop(columns=[RawTrip.MEMBER_CASUAL]).assign(extra=1)
    df[RawTrip.STARTED_AT] = df[RawTrip.STARTED_AT].astype(str)
    df.loc[0, RawTrip.START_STATION_ID] = None

    with pytest.raises(SchemaError) as error:
        validate(df)

    problems = "\n".join(error.value.problems)
    assert "missing columns: ['member_casual']" in problems
    assert "unexpected columns: ['extra']" in problems
    assert "started_at: expected naive datetime" in problems
    assert "start_station_id: 1 missing values" in problems


def test_validate_rejects_timezone_aware_datetimes():
    df = read()
    df[RawTrip.ENDED_AT] = df[RawTrip.ENDED_AT].dt.tz_localize("America/New_York")
    with pytest.raises(SchemaError, match="ended_at"):
        validate(df)
