"""Engine construction (SQLAlchemy 2 Core over psycopg 3)."""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.config import get_settings


def _json_default(obj: Any) -> Any:
    # Decimals go into JSON as strings so they never pass through float.
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


def json_loads(text: str) -> Any:
    # JSON numbers with a fraction come back as Decimal, never float.
    return json.loads(text, parse_float=Decimal)


def make_engine(url: str) -> Engine:
    return sa.create_engine(
        url, json_serializer=json_dumps, json_deserializer=json_loads, pool_pre_ping=True
    )


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)
