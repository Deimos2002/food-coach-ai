"""Weights, profile and targets routes. Every route requires the authenticated user."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.engine import Engine

from app.api.auth import CurrentUser, get_current_user
from app.db.engine import get_engine
from app.services import profile as profile_svc
from app.services import weights as weights_svc
from app.services.privacy import require_generation

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


def get_now() -> datetime:
    return datetime.now(UTC)


_user = Depends(get_current_user)
_engine = Depends(get_db_engine)
_now = Depends(get_now)


_ACTIVITY_LEVELS = (Decimal("1.2"), Decimal("1.375"), Decimal("1.55"), Decimal("1.725"))


class WeightIn(BaseModel):
    # Lower bound is just "positive", not a plausible-adult-weight floor: A19 requires
    # that a typo such as 7.5 kg (meant as 72.5) is accepted and later fixed via
    # ?replace=true, with the trend recomputing from the correction.
    kg: Decimal = Field(gt=0, lt=400)


class ProfileIn(BaseModel):
    sex: Literal["male", "female"]
    # No one using this app today was born before 1900, and the minimum age (10) rules
    # out typos that land in the present.
    birth_year: int = Field(ge=1900, le=datetime.now(UTC).year - 10)
    height_cm: Decimal = Field(gt=100, lt=250)
    # Spec 11.2/A: one of the four Mifflin-St Jeor activity factors, no others.
    activity: Decimal
    goal: Literal["lose", "maintain", "gain"]
    # Global safety cap (spec: deficit <= 25%, planned loss <= 1% of body weight/week).
    pace_pct_per_week: Decimal = Field(gt=0, le=1)

    @field_validator("activity")
    @classmethod
    def _activity_is_a_known_level(cls, v: Decimal) -> Decimal:
        if v not in _ACTIVITY_LEVELS:
            raise ValueError(f"activity must be one of {[str(a) for a in _ACTIVITY_LEVELS]}")
        return v


@router.put("/weights/{day}")
def put_weight(
    day: date,
    body: WeightIn,
    replace: bool = False,
    user: CurrentUser = _user,
    engine: Engine = _engine,
):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return weights_svc.put_weight(conn, day, body.kg, replace, generation)
    except weights_svc.WeightError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.delete("/weights/{day}", status_code=204)
def delete_weight(day: date, user: CurrentUser = _user, engine: Engine = _engine):
    with engine.begin() as conn:
        require_generation(conn, user.oid)
        weights_svc.delete_weight(conn, day)


@router.post("/weights/{day}/confirm")
def confirm_weight(day: date, user: CurrentUser = _user, engine: Engine = _engine):
    try:
        with engine.begin() as conn:
            generation = require_generation(conn, user.oid)
            return weights_svc.confirm_weight(conn, day, generation)
    except weights_svc.WeightError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.get("/weights/trend")
def get_trend(engine: Engine = _engine):
    with engine.connect() as conn:
        return weights_svc.trend_points_json(weights_svc.trend(conn))


@router.get("/profile")
def get_profile(engine: Engine = _engine):
    with engine.connect() as conn:
        result = profile_svc.get_profile(conn)
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "no profile set"})
    return result


@router.put("/profile")
def put_profile(body: ProfileIn, user: CurrentUser = _user, engine: Engine = _engine):
    with engine.begin() as conn:
        generation = require_generation(conn, user.oid)
        return profile_svc.put_profile(
            conn,
            body.sex,
            body.birth_year,
            body.height_cm,
            body.activity,
            body.goal,
            body.pace_pct_per_week,
            generation,
        )


@router.get("/targets")
def get_targets(day: date, engine: Engine = _engine):
    try:
        with engine.connect() as conn:
            return profile_svc.get_targets(conn, day)
    except profile_svc.ProfileError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
