"""Applies the SQL migrations in ``migrations/`` with yoyo.

yoyo records applied migrations in its own tables (``_yoyo_migration``), like
Flyway's ``flyway_schema_history``: each migration runs once, in its own
transaction, and the database is never dropped.
"""

import logging
from pathlib import Path

from yoyo import get_backend, read_migrations

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply all pending migrations and return their ids.

    Takes a lock in the database, so concurrent starts cannot apply the same
    migration twice.
    """
    backend = get_backend(database_url)
    migrations = read_migrations(str(migrations_dir))
    with backend.lock():
        pending = backend.to_apply(migrations)
        backend.apply_migrations(pending)
    applied = [m.id for m in pending]
    log.info("Database migrations applied: %s", applied or "none pending")
    return applied
