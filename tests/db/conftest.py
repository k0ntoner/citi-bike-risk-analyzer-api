"""PostgreSQL for integration tests, started once per test session in Docker.

The schema is created by the real yoyo migrations, as on application startup.
Tables are emptied after every test. Without Docker the tests are skipped
locally, but fail in CI (``CI=true``) so they cannot be skipped silently.
"""

import os

import docker
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from testcontainers.community.postgres import PostgresContainer

from bikeflow.storage.migrations import apply_migrations


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def database_url():
    if not _docker_available():
        if os.environ.get("CI"):
            pytest.fail("Docker is required for database tests in CI")
        pytest.skip("Docker is not running: database tests skipped")
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        apply_migrations(url)
        yield url


@pytest.fixture(scope="session")
def engine(database_url):
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    yield sessionmaker(engine, expire_on_commit=False)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE pipeline_run, pipeline_step RESTART IDENTITY CASCADE"))
