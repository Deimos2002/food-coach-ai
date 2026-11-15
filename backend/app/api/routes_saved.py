"""Saved meals and explicit aliases routes. Every route requires the authenticated user."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from app.api.auth import CurrentUser, get_current_user
from app.db.engine import get_engine
from app.domain.quantity import Unit
from app.services import saved as svc
from app.services.privacy import require_generation

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


def get_now() -> datetime:
    return datetime.now(UTC)


_user = Depends(get_current_user)
_engine = Depends(get_db_engine)
_now = Depends(get_now)


class SavedItemIn(BaseModel):
    product_id: int
    amount: Decimal
    unit: Unit


class SavedMealIn(BaseModel):
    name: str
    items: list[SavedItemIn]


class UseSavedMealIn(BaseModel):
    meal_id: int


class AliasIn(BaseModel):
    text: str
    product_id: int


def _error(exc: svc.SavedError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.get("/saved-meals")
def list_saved_meals(engine: Engine = _engine):
    with engine.connect() as conn:
        return svc.list_saved_meals(conn)


@router.post("/saved-meals", status_code=201)
def create_saved_meal(body: SavedMealIn, user: CurrentUser = _user, engine: Engine = _engine):
    with engine.begin() as conn:
        generation = require_generation(conn, user.oid)
        items = [svc.SavedItemIn(it.product_id, it.amount, it.unit) for it in body.items]
        meal_id = svc.create_saved_meal(conn, body.name, items, generation)
    return {"saved_meal_id": meal_id}


@router.post("/saved-meals/{id}/use")
def use_saved_meal(
    id: int,
    body: UseSavedMealIn,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=200),
    user: CurrentUser = _user,
    engine: Engine = _engine,
    now: datetime = _now,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.use_saved_meal(
                conn, id, body.meal_id, generation, now, idempotency_key
            )
    except svc.SavedError as exc:
        return _error(exc)


@router.post("/aliases", status_code=201)
def create_alias(body: AliasIn, user: CurrentUser = _user, engine: Engine = _engine):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            alias_id = svc.create_alias(conn, body.text, body.product_id, generation)
    except svc.SavedError as exc:
        return _error(exc)
    return {"alias_id": alias_id}


@router.put("/aliases/{id}")
def update_alias(id: int, body: AliasIn, user: CurrentUser = _user, engine: Engine = _engine):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            svc.update_alias(conn, id, body.text, body.product_id, generation)
    except svc.SavedError as exc:
        return _error(exc)
    return {"alias_id": id}


@router.get("/aliases")
def list_aliases(engine: Engine = _engine):
    with engine.connect() as conn:
        return svc.list_aliases(conn)
