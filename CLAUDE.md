# BikeFlow: Citi Bike station risk analyzer

Predicts the risk that a Jersey City / Hoboken Citi Bike station becomes empty or
full within the next hour. A model is trained on the monthly trip history to
predict, for each station and time interval, how many bikes depart from and arrive
at the station during that interval (one hour for now; the interval length is not
final). At prediction time the live GBFS station state is the starting point:
bikes now + predicted arrivals − predicted departures.

## Commands

```bash
uv sync              # install dependencies
uv run pytest        # run tests
uv add <package>     # add a dependency (never pip install, never edit pyproject by hand)
```

## Data

Before working with trip data, read `docs/data-notes.md`: known quirks of the
dataset with numbers (file split by `ended_at`, out-of-network trips, missing end
stations, local time without zone, weather impact). Add new findings there, with
the month they were observed in.

Local data lives in `data/` (git-ignored). Load a trip CSV with
`bikeflow.contracts.raw_trips.read_raw_trips_csv(path)`.

## Architecture

Pipeline run: INGEST → ANALYZE_RAW → PROCESS → ANALYZE_FEATURES → TRAIN.

```
bikeflow/
  config.py      settings from env vars; read only by the orchestrator
  contracts/     input/output contracts of the steps, raw trip schema
  steps/         one module per step, pure logic
  service/       orchestrator (BikeFlowPipelineService), PipelineRunService, DTOs, mapper
  storage/       ORM entities and repositories (PostgreSQL via SQLAlchemy)
migrations/      SQL migrations, the source of truth for the DB schema
docs/            shared notes
```

## Rules

**Steps**
- Steps are independent. `bikeflow/steps/*` and `bikeflow/contracts/*` must not import
  `bikeflow.storage`, `bikeflow.service` or `bikeflow.config`. A step gets everything
  through its input contract and returns its result; the orchestrator does all
  persistence and passes configuration in.
- Never modify an input DataFrame in place: ANALYZE_RAW and PROCESS receive the same
  object. Work on copies or derived frames.
- Use the `RawTrip` column constants instead of string literals.
- ANALYZE_RAW and ANALYZE_FEATURES are informational: they describe data, never change
  it, and return structured data (tables, values), never HTML. HTML is rendered on the
  fly by the app.
- PROCESS validates defensively, including problems absent from the current data
  (duplicates, end before start, DST), and does not depend on the analysis steps.

**Model features**
- Only features available at prediction time: time (hour, weekday, month, holiday),
  station, weather. No recent-trip lags: trips are published monthly.
- No features that identify a specific date (day of month, day of year, year).
- Validate on a time-held-out period and compare with a calendar-profile baseline.

**Database**
- The schema is owned by `migrations/*.sql`. Never call `Base.metadata.create_all()`.
  A schema change is a new migration first, then the matching change in
  `storage/models.py`.
- Repositories never commit; the service layer owns transactions.
- Relationships use `lazy="raise_on_sql"`: load them explicitly (`selectinload`).
- Only the storage layer sees ORM entities; everything else uses DTOs from
  `service/dto.py`. DTOs are immutable: change them with `model_copy(update=...)`.
- Large data (tables, models) goes to file storage; the DB stores only its keys.

**Style**
- Docstrings on every class and public function, describing what it is for, so
  teammates can follow the code.
- All timestamps stored in the DB are timezone-aware UTC.
