"""Monthly LLM budget (parent spec §7.5, weekly-coach §3.1 step 4 and 7).

A call first *reserves* its worst-case cost in one atomic statement; afterwards the
reservation is either *settled* (actual cost moved to ``spent_eur``) or *released*.
Two parallel reservations with budget for one: exactly one gets a row back (A20).
All amounts are ``Decimal``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.engine import Connection

_RESERVE = sa.text(
    "INSERT INTO llm_budget (period_start, cap_eur, reserved_eur, spent_eur) "
    "SELECT :period, CAST(:cap AS numeric), CAST(:max AS numeric), 0 "
    "WHERE CAST(:max AS numeric) <= CAST(:cap AS numeric) "
    "ON CONFLICT (period_start) DO UPDATE "
    "SET reserved_eur = llm_budget.reserved_eur + EXCLUDED.reserved_eur, "
    "cap_eur = EXCLUDED.cap_eur "
    "WHERE llm_budget.spent_eur + llm_budget.reserved_eur + EXCLUDED.reserved_eur "
    "<= EXCLUDED.cap_eur "
    "RETURNING period_start"
)
_SETTLE = sa.text(
    "UPDATE llm_budget SET reserved_eur = GREATEST(reserved_eur - CAST(:reserved AS numeric), 0), "
    "spent_eur = spent_eur + CAST(:actual AS numeric) WHERE period_start = :period"
)


def period_of(day: date) -> date:
    return day.replace(day=1)


def reserve(conn: Connection, max_cost: Decimal, cap: Decimal, today: date) -> bool:
    """Reserve ``max_cost`` against this month's ``cap``. False means refused."""
    row = conn.execute(
        _RESERVE, {"period": period_of(today), "cap": cap, "max": max_cost}
    ).first()
    return row is not None


def settle(conn: Connection, reserved: Decimal, actual: Decimal, today: date) -> None:
    """Close a reservation: drop ``reserved`` and add the ``actual`` cost to spent."""
    conn.execute(_SETTLE, {"period": period_of(today), "reserved": reserved, "actual": actual})


def release(conn: Connection, reserved: Decimal, today: date) -> None:
    """Return a reservation unused (nothing was spent)."""
    settle(conn, reserved, Decimal(0), today)
