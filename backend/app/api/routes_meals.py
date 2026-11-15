"""Meals, meal items and day status routes. Every route requires the authenticated user."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from app.api.auth import CurrentUser, get_current_user
from app.db.engine import get_engine
from app.services import logging as svc
from app.services.privacy import require_generation

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


def get_now() -> datetime:
    return datetime.now(UTC)


_user = Depends(get_current_user)
_engine = Depends(get_db_engine)
_now = Depends(get_now)


class MealIn(BaseModel):
    # The client's local date: the server never derives it from its own (UTC) clock.
    day: date
    meal_type: svc.MealType


class SkippedIn(BaseModel):
    skipped: bool


class MoveIn(BaseModel):
    meal_id: int


class DayStatusIn(BaseModel):
    status: Literal["complete", "partial"]


def _error(exc: svc.LoggingError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body)


@router.post("/meals", status_code=201)
def create_meal(body: MealIn, user: CurrentUser = _user, engine: Engine = _engine):
    with engine.begin() as conn:
        generation = require_generation(conn, user.oid)
        return {"meal_id": svc.create_meal(conn, body.day, body.meal_type, generation)}


@router.post("/meals/{meal_id}/items", status_code=201)
def add_item(
    meal_id: int,
    body: svc.AddItemIn,
    idempotency_key: str = Header(min_length=1, max_length=200),
    user: CurrentUser = _user,
    engine: Engine = _engine,
    now: datetime = _now,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.add_item(conn, meal_id, idempotency_key, body, generation, now)
    except svc.LoggingError as exc:
        return _error(exc)


@router.patch("/meal-items/{item_id}")
def patch_item(
    item_id: int,
    body: svc.PatchItemIn,
    user: CurrentUser = _user,
    engine: Engine = _engine,
    now: datetime = _now,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.patch_item(conn, item_id, body, generation, now)
    except svc.LoggingError as exc:
        return _error(exc)


@router.delete("/meal-items/{item_id}", status_code=204)
def delete_item(
    item_id: int, user: CurrentUser = _user, engine: Engine = _engine, now: datetime = _now
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            svc.delete_item(conn, item_id, generation, now)
    except svc.LoggingError as exc:
        return _error(exc)
    return Response(status_code=204)


@router.post("/meal-items/{item_id}/move")
def move_item(
    item_id: int,
    body: MoveIn,
    user: CurrentUser = _user,
    engine: Engine = _engine,
    now: datetime = _now,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.move_item(conn, item_id, body.meal_id, generation, now)
    except svc.LoggingError as exc:
        return _error(exc)


@router.put("/meals/{meal_id}/skipped")
def set_skipped(meal_id: int, body: SkippedIn, user: CurrentUser = _user, engine: Engine = _engine):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.set_skipped(conn, meal_id, body.skipped, generation)
    except svc.LoggingError as exc:
        return _error(exc)


@router.get("/recent-items")
def recent_items(
    limit: int = Query(default=20, ge=1, le=50), engine: Engine = _engine
):
    with engine.connect() as conn:
        return svc.recent_items(conn, limit)


@router.get("/days/{day}")
def get_day(day: date, engine: Engine = _engine):
    # One read-only REPEATABLE READ transaction: the status and the items come from the
    # same snapshot, so a concurrent delete can never pair a stale "complete" status with
    # the reduced items (A.3, A17).
    with svc.snapshot_connection(engine) as conn:
        return svc.get_day(conn, day)


@router.put("/days/{day}/status")
def set_day_status(
    day: date,
    body: DayStatusIn,
    user: CurrentUser = _user,
    engine: Engine = _engine,
    now: datetime = _now,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return svc.set_day_status(conn, day, body.status, generation, now)
    except svc.LoggingError as exc:
        return _error(exc)
