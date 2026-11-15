"""Weight entries and their EWMA trend (Task 7 domain, Task 14 persistence).

Table definitions mirror migration ``0004_body_saved`` and live in their own MetaData,
following the pattern set by ``app/services/logging.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.domain.weights import TrendPoint, WeightEntry, compute_trend

metadata = sa.MetaData()

weight_entry = sa.Table(
    "weight_entry",
    metadata,
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("kg", sa.Numeric(asdecimal=True), nullable=False),
    sa.Column("confirmed", sa.Boolean, nullable=False),
    sa.Column("data_generation", sa.Integer, nullable=False),
)


class WeightError(Exception):
    """An error with an HTTP status and a JSON body, raised before anything is committed."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _entries(conn: Connection) -> list[WeightEntry]:
    rows = conn.execute(
        sa.select(weight_entry.c.day, weight_entry.c.kg, weight_entry.c.confirmed).order_by(
            weight_entry.c.day
        )
    ).all()
    return [WeightEntry(r.day, r.kg, r.confirmed) for r in rows]


def put_weight(
    conn: Connection, day: date, kg: Decimal, replace: bool, generation: int
) -> dict[str, Any]:
    """Insert-or-replace a day's weight, race-safe against a concurrent first write.

    Two parallel first-time PUTs for the same day must both succeed with no error: a
    check-then-insert (``SELECT`` then ``INSERT``) leaves a window where both requests
    see "no row" and one of them would otherwise crash on the unique primary key. The
    single ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` closes that window — the
    database itself picks exactly one winner. If nothing comes back, a row already
    existed (written either before this call started or by the loser of a race that
    happened inside it); that is a 409 unless the caller asked to ``replace``, in which
    case an explicit ``UPDATE`` applies.
    """
    inserted = conn.execute(
        pg_insert(weight_entry)
        .values(day=day, kg=kg, confirmed=False, data_generation=generation)
        .on_conflict_do_nothing(index_elements=[weight_entry.c.day])
        .returning(weight_entry.c.day)
    ).scalar_one_or_none()
    if inserted is None:
        if not replace:
            raise WeightError(
                409, "a weight entry already exists for this day; use ?replace=true"
            )
        conn.execute(
            sa.update(weight_entry)
            .where(weight_entry.c.day == day)
            .values(kg=kg, confirmed=False, data_generation=generation)
        )
    return {"day": day.isoformat(), "kg": str(kg)}


def delete_weight(conn: Connection, day: date) -> None:
    conn.execute(sa.delete(weight_entry).where(weight_entry.c.day == day))


def confirm_weight(conn: Connection, day: date, generation: int) -> dict[str, Any]:
    updated = conn.execute(
        sa.update(weight_entry)
        .where(weight_entry.c.day == day)
        .values(confirmed=True, data_generation=generation)
        .returning(weight_entry.c.day)
    ).scalar_one_or_none()
    if updated is None:
        raise WeightError(404, "no weight entry for this day")
    return {"day": day.isoformat(), "confirmed": True}


def trend(conn: Connection) -> list[TrendPoint]:
    return compute_trend(_entries(conn))


def trend_on_or_before(conn: Connection, day: date) -> Decimal | None:
    points = [p for p in trend(conn) if p.day <= day]
    if not points:
        return None
    return points[-1].trend


def trend_points_json(points: list[TrendPoint]) -> list[dict[str, Any]]:
    return [
        {
            "day": p.day.isoformat(),
            "trend": str(p.trend),
            "flagged": p.flagged,
            "suggest_confirm": p.suggest_confirm,
        }
        for p in points
    ]
