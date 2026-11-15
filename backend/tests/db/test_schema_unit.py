"""Database-free checks of the Task 8 persistence layer.

Covers the pure row <-> NutrientValue mapping, the SQLAlchemy table metadata, and the
SQL rendered by ``alembic upgrade head --sql`` (offline mode, no server needed).
"""

from __future__ import annotations

import io
import re
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.db import tables
from app.db.catalogue import row_to_value, value_to_row
from app.db.engine import json_dumps, json_loads
from app.domain.identity import Status
from app.domain.values import (
    Derivation,
    Nutrient,
    NutrientValue,
    Qualifier,
    Range,
    Source,
    Verification,
)

BACKEND = Path(__file__).resolve().parents[2]

VALUES = [
    NutrientValue.point(Decimal("12.5"), Source.CIQUAL),
    NutrientValue.point(Decimal(0), Source.LABEL),
    NutrientValue(True, Range(Decimal(0), None), Qualifier.TRACE, Source.CIQUAL),
    NutrientValue(
        True,
        Range(Decimal(0), Decimal("0.5")),
        Qualifier.LESS_THAN,
        Source.CIQUAL,
        Derivation.CONVERTED,
        Verification.FLAGGED,
    ),
    NutrientValue.unknown(Source.OFF_CONTRIBUTOR),
]


@pytest.mark.parametrize("value", VALUES)
def test_value_row_roundtrip(value):
    row = value_to_row(value)
    assert row_to_value(row) == value


def test_unknown_stores_no_range_and_known_zero_is_point_zero():
    unknown = value_to_row(NutrientValue.unknown(Source.MANUAL))
    assert unknown["known"] is False
    assert unknown["lower"] is None and unknown["upper"] is None

    zero = value_to_row(NutrientValue.point(Decimal(0), Source.LABEL))
    assert zero["known"] is True
    assert zero["lower"] == Decimal(0) and zero["upper"] == Decimal(0)


def test_trace_keeps_null_upper():
    row = value_to_row(NutrientValue(True, Range(Decimal(0), None), Qualifier.TRACE, Source.CIQUAL))
    assert row["lower"] == Decimal(0)
    assert row["upper"] is None
    assert row["qualifier"] == "trace"


def test_row_values_are_plain_strings_and_decimals():
    row = value_to_row(VALUES[3])
    assert type(row["qualifier"]) is str
    assert type(row["source"]) is str
    assert isinstance(row["upper"], Decimal)


EXPECTED_TABLES = {
    "user_account",
    "product",
    "source_record",
    "profile_revision",
    "nutrient_value",
    "nutrient_override",
    "portion",
    "catalogue_release",
    "active_release",
    "product_state",
}


def test_metadata_has_contract_tables():
    assert set(tables.metadata.tables) == EXPECTED_TABLES


def test_no_float_columns():
    for table in tables.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, sa.Float), f"{table.name}.{column.name}"
            if isinstance(column.type, sa.Numeric):
                assert column.type.asdecimal, f"{table.name}.{column.name}"


def _offline_config() -> tuple[Config, io.StringIO]:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://u:p@localhost:5432/offline")
    cfg.attributes["configure_logger"] = False
    buf = io.StringIO()
    cfg.output_buffer = buf
    return cfg, buf


@pytest.fixture(scope="module")
def offline_sql() -> str:
    cfg, buf = _offline_config()
    command.upgrade(cfg, "head", sql=True)
    return buf.getvalue()


@pytest.fixture(scope="module")
def offline_downgrade_sql() -> str:
    cfg, buf = _offline_config()
    command.downgrade(cfg, "0001_initial:base", sql=True)
    return buf.getvalue()


def _create_block(sql: str, table: str) -> str:
    match = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", sql, re.DOTALL)
    assert match, f"no CREATE TABLE for {table}"
    return match.group(1)


def test_offline_migration_creates_extensions(offline_sql):
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in offline_sql
    assert "CREATE EXTENSION IF NOT EXISTS unaccent" in offline_sql


def test_offline_migration_matches_metadata(offline_sql):
    for name, table in tables.metadata.tables.items():
        block = _create_block(offline_sql, name)
        for column in table.columns:
            assert re.search(rf"^\s+{column.name} ", block, re.MULTILINE), f"{name}.{column.name}"


def test_offline_migration_never_uses_float(offline_sql):
    assert not re.search(r"\b(FLOAT|REAL|DOUBLE PRECISION)\b", offline_sql)


def test_offline_migration_product_state_constraints(offline_sql):
    block = _create_block(offline_sql, "product_state")
    assert "NULLS NOT DISTINCT (release_id, product_id)" in block
    assert f"status <> '{Status.MERGED.value}' OR merged_into IS NOT NULL" in block


def test_offline_migration_enum_checks_use_domain_values(offline_sql):
    block = _create_block(offline_sql, "nutrient_value")
    for enum in (Nutrient, Qualifier, Source, Derivation, Verification):
        for member in enum:
            assert f"'{member.value}'" in block


def test_offline_revision_status_is_lifecycle_only(offline_sql):
    block = _create_block(offline_sql, "profile_revision")
    assert "status IN ('adopted', 'pending_review', 'rejected')" in block
    assert "'active'" not in block and "'superseded'" not in block
    assert "uq_profile_revision_one_active" not in offline_sql
    assert "UNIQUE (product_id, nutrition_hash)" in block


def test_offline_cooking_additions_only_for_user_dishes(offline_sql):
    block = _create_block(offline_sql, "product")
    assert "(kind = 'dish' AND owner = 'user') = (includes_cooking_additions IS NOT NULL)" in block


def test_offline_override_version_unique(offline_sql):
    block = _create_block(offline_sql, "nutrient_override")
    assert "UNIQUE (product_id, nutrient, version)" in block


def test_offline_downgrade_keeps_extensions(offline_downgrade_sql):
    assert "DROP TABLE product_state" in offline_downgrade_sql
    assert "DROP EXTENSION" not in offline_downgrade_sql


def test_json_loads_parses_floats_as_decimal():
    value = json_loads('{"sugars_100g": 11.5, "n": 3, "s": "x"}')
    assert value == {"sugars_100g": Decimal("11.5"), "n": 3, "s": "x"}
    assert isinstance(value["sugars_100g"], Decimal)


def test_json_dumps_writes_decimal_as_string():
    assert json_dumps({"a": Decimal("0.1")}) == '{"a": "0.1"}'


def test_metadata_mirrors_lifecycle_and_unique_constraints():
    names = {
        c.name
        for t in tables.metadata.tables.values()
        for c in t.constraints
        if isinstance(c, sa.UniqueConstraint)
    }
    assert {
        "uq_profile_revision_product_hash",
        "uq_nutrient_override_product_nutrient_version",
        "uq_product_state_release_product",
    } <= names
