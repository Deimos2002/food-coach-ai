"""Profile and targets (Task 7 domain, Task 14 persistence).

Table definitions mirror migration ``0004_body_saved`` and live in their own MetaData.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.domain.targets import Profile, Targets, compute_targets
from app.services.weights import trend_on_or_before

metadata = sa.MetaData()

profile_table = sa.Table(
    "profile",
    metadata,
    sa.Column("singleton", sa.Boolean, primary_key=True),
    sa.Column("sex", sa.Text, nullable=False),
    sa.Column("birth_year", sa.Integer, nullable=False),
    sa.Column("height_cm", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("activity", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("goal", sa.Text, nullable=False),
    sa.Column("pace_pct_per_week", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
)


class ProfileError(Exception):
    """An error with an HTTP status and a detail message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _row_to_profile(row: Any) -> Profile:
    return Profile(
        sex=row.sex,
        birth_year=row.birth_year,
        height_cm=row.height_cm,
        activity=row.activity,
        goal=row.goal,
        pace_pct_per_week=row.pace_pct_per_week,
    )


def get_profile(conn: Connection) -> dict[str, Any] | None:
    row = conn.execute(sa.select(profile_table)).mappings().first()
    if row is None:
        return None
    return {
        "sex": row["sex"],
        "birth_year": row["birth_year"],
        "height_cm": str(row["height_cm"]),
        "activity": str(row["activity"]),
        "goal": row["goal"],
        "pace_pct_per_week": str(row["pace_pct_per_week"]),
    }


def put_profile(
    conn: Connection,
    sex: str,
    birth_year: int,
    height_cm: Decimal,
    activity: Decimal,
    goal: str,
    pace_pct_per_week: Decimal,
    generation: int,
) -> dict[str, Any]:
    values = {
        "singleton": True,
        "sex": sex,
        "birth_year": birth_year,
        "height_cm": height_cm,
        "activity": activity,
        "goal": goal,
        "pace_pct_per_week": pace_pct_per_week,
        "data_generation": generation,
    }
    conn.execute(
        pg_insert(profile_table)
        .values(**values)
        .on_conflict_do_update(index_elements=[profile_table.c.singleton], set_=values)
    )
    return get_profile(conn)  # type: ignore[return-value]


def get_targets(conn: Connection, day: date) -> dict[str, Any]:
    row = conn.execute(sa.select(profile_table)).mappings().first()
    if row is None:
        raise ProfileError(404, "no profile set")
    trend_kg = trend_on_or_before(conn, day)
    if trend_kg is None:
        raise ProfileError(404, "no weight entry on or before this day")
    profile = _row_to_profile(row)
    targets: Targets = compute_targets(profile, trend_kg, day)
    return {
        "maintenance_kcal": targets.maintenance_kcal,
        "kcal": targets.kcal,
        "protein_g": targets.protein_g,
    }
