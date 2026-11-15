"""Week screen routes: guideline cards (coach Task 3, spec §2.3) and the week (Task 4, §2.7).

Cards are reference data, not user data: verifying one only sets ``verified_by_user_at``.
A rule may cite a card only once it is verified (A37).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from app.api.auth import get_current_user
from app.coach.factsheet import build_fact_sheet, complete_days
from app.db.engine import get_engine
from app.domain.week_rules import iso_week_days
from app.services.logging import snapshot_connection
from app.services.privacy import jsonable

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


_engine = Depends(get_db_engine)

_COLUMNS = (
    "id, source_org, title, quote, url, retrieved_on, threshold, unit, notes, verified_by_user_at"
)


def _row(row: Any) -> dict[str, Any]:
    return {k: jsonable(v) for k, v in row.items()}


@router.get("/cards")
def list_cards(engine: Engine = _engine) -> list[dict[str, Any]]:
    with engine.connect() as c:
        rows = c.execute(sa.text(f"SELECT {_COLUMNS} FROM guideline_card ORDER BY id")).mappings()
        return [_row(r) for r in rows]


_VERIFY = sa.text(
    "UPDATE guideline_card SET verified_by_user_at = now() WHERE id = :id RETURNING " + _COLUMNS
)
_UNVERIFY = sa.text(
    "UPDATE guideline_card SET verified_by_user_at = NULL WHERE id = :id RETURNING " + _COLUMNS
)


def _set_verified(engine: Engine, card_id: str, statement: sa.TextClause) -> Any:
    with engine.begin() as c:
        row = c.execute(statement, {"id": card_id}).mappings().one_or_none()
    if row is None:
        return JSONResponse(status_code=404, content={"detail": "card not found"})
    return _row(row)


@router.post("/cards/{card_id}/verify")
def verify_card(card_id: str, engine: Engine = _engine) -> Any:
    return _set_verified(engine, card_id, _VERIFY)


@router.post("/cards/{card_id}/unverify")
def unverify_card(card_id: str, engine: Engine = _engine) -> Any:
    return _set_verified(engine, card_id, _UNVERIFY)


@router.get("/week/{iso_week}")
def get_week(iso_week: str, engine: Engine = _engine) -> Any:
    """The week's fact sheet (with ``item_refs`` for the screen), complete-day count and cards.

    One read-only snapshot, so the sheet, the count and the cards agree.
    """
    try:
        iso_week_days(iso_week)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    with snapshot_connection(engine) as conn:
        sheet = build_fact_sheet(conn, iso_week)
        count = complete_days(conn, iso_week)
        cards = [
            _row(r)
            for r in conn.execute(
                sa.text(f"SELECT {_COLUMNS} FROM guideline_card ORDER BY id")
            ).mappings()
        ]
    return {"fact_sheet": sheet, "complete_days": count, "cards": cards}
