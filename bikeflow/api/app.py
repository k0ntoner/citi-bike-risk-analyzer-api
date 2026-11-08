"""Composition root: builds every service and wires them into the FastAPI app.

This is the Python counterpart of a Spring application context: all objects are
created here explicitly, once, and handed to their users through constructors.
Routes reach them via ``request.app.state``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bikeflow import config
from bikeflow.api import health, runs
from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.steps.analyze_features import AnalyzeFeaturesService
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest.source import S3HttpTripDataSource
from bikeflow.steps.ingest_service import IngestService
from bikeflow.steps.process import ProcessService
from bikeflow.steps.train import TrainService
from bikeflow.storage.migrations import apply_migrations


def create_app(
    database_url: str = config.DATABASE_URL,
    migrate: bool = config.MIGRATE_ON_STARTUP,
) -> FastAPI:
    """Build the application with all its services.

    On startup pending SQL migrations are applied, like Flyway in Spring Boot.
    Apart from that the database engine is lazy: no connection is opened until
    the first query.

    Args:
        database_url: SQLAlchemy URL of the database; tests can pass their own.
        migrate: Apply migrations on startup; tests without a database pass False.
    """
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    run_service = PipelineRunService(session_factory)
    pipeline = BikeFlowPipelineService(
        run_service,
        IngestService(S3HttpTripDataSource(config.TRIPDATA_URL)),
        AnalyzeRawService(),
        ProcessService(),
        AnalyzeFeaturesService(),
        TrainService(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if migrate:
            apply_migrations(database_url)
        yield
        engine.dispose()  # close pooled connections on shutdown

    app = FastAPI(title="BikeFlow", lifespan=lifespan)
    app.state.run_service = run_service
    app.state.pipeline = pipeline
    app.include_router(health.router)
    app.include_router(runs.router)
    return app


app = create_app()
"""Instance used by uvicorn."""
