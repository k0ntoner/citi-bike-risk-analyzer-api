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
from bikeflow.api import health
from bikeflow.service.bikeflow_pipeline_service import BikeFlowPipelineService
from bikeflow.service.pipeline_run_service import PipelineRunService
from bikeflow.steps.analyze_features import AnalyzeFeaturesService
from bikeflow.steps.analyze_raw import AnalyzeRawService
from bikeflow.steps.ingest import IngestService
from bikeflow.steps.process import ProcessService
from bikeflow.steps.train import TrainService


def create_app(database_url: str = config.DATABASE_URL) -> FastAPI:
    """Build the application with all its services.

    The database engine is lazy: no connection is opened until the first query,
    so the app starts even when the database is not reachable.

    Args:
        database_url: SQLAlchemy URL of the database; tests can pass their own.
    """
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    run_service = PipelineRunService(session_factory)
    pipeline = BikeFlowPipelineService(
        run_service,
        IngestService(),
        AnalyzeRawService(),
        ProcessService(),
        AnalyzeFeaturesService(),
        TrainService(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()  # close pooled connections on shutdown

    app = FastAPI(title="BikeFlow", lifespan=lifespan)
    app.state.run_service = run_service
    app.state.pipeline = pipeline
    app.include_router(health.router)
    return app


app = create_app()
"""Instance used by uvicorn."""
