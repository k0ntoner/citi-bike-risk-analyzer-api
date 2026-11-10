"""The ORM models match the schema created by the migrations (like ddl-auto=validate).

Fails when a migration and ``storage/models.py`` drift apart: a column, type,
nullability, key, constraint, index or enum value changed in one but not the other.
"""

import re

import pytest
from sqlalchemy import UniqueConstraint, inspect
from sqlalchemy.dialects import postgresql

from bikeflow.storage.models import Base, RunStatus, StepStatus, StepType, Trigger

TABLES = sorted(Base.metadata.tables)
DIALECT = postgresql.dialect()


@pytest.fixture(scope="module")
def db(engine):
    return inspect(engine)


def _type(t) -> str:
    return t.compile(dialect=DIALECT)


@pytest.mark.parametrize("table", TABLES)
def test_columns(db, table):
    model = {c.name: (_type(c.type), c.nullable) for c in Base.metadata.tables[table].columns}
    actual = {c["name"]: (_type(c["type"]), c["nullable"]) for c in db.get_columns(table)}
    assert actual == model


@pytest.mark.parametrize("table", TABLES)
def test_keys_constraints_and_indexes(db, table):
    model = Base.metadata.tables[table]

    assert db.get_pk_constraint(table)["constrained_columns"] == [c.name for c in model.primary_key]
    assert {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in db.get_foreign_keys(table)} == {
        ((fk.parent.name,), fk.column.table.name, (fk.column.name,)) for fk in model.foreign_keys
    }
    assert {tuple(u["column_names"]) for u in db.get_unique_constraints(table)} == {
        tuple(c.name for c in u.columns) for u in model.constraints if isinstance(u, UniqueConstraint)
    }
    # PostgreSQL backs every UNIQUE constraint with an index; those are checked above.
    indexes = {i["name"] for i in db.get_indexes(table) if "duplicates_constraint" not in i}
    assert indexes == {i.name for i in model.indexes}


@pytest.mark.parametrize("table, column, enum", [
    ("pipeline_run", "status", RunStatus),
    ("pipeline_run", "trigger", Trigger),
    ("pipeline_step", "step_type", StepType),
    ("pipeline_step", "status", StepStatus),
])
def test_enum_values_match_check_constraints(db, table, column, enum):
    checks = [c["sqltext"] for c in db.get_check_constraints(table) if column in c["sqltext"]]
    assert len(checks) == 1, f"expected one CHECK on {table}.{column}, got {checks}"
    assert set(re.findall(r"'([^']+)'", checks[0])) == {e.value for e in enum}
