"""Weekly coach routes (weekly-coach spec §3.1, §3.6). Auth is enforced at the router level.

``get_llm_client`` builds the Azure client lazily; tests override it with FakeLlmClient.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from app.api.auth import CurrentUser, get_current_user
from app.coach import service
from app.coach.llm import AzureLlmClient, LlmClient
from app.config import Settings
from app.db.engine import get_engine
from app.domain.week_rules import iso_week_days

router = APIRouter(prefix="/coach", dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def get_llm_client(request: Request) -> LlmClient:
    return AzureLlmClient(_settings(request))


_engine = Depends(get_db_engine)
_llm = Depends(get_llm_client)
_user = Depends(get_current_user)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _bad_week(iso_week: str) -> JSONResponse | None:
    try:
        iso_week_days(iso_week)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return None


def _out(result: dict[str, Any]) -> Any:
    if result.get("status") == "in_progress":
        return JSONResponse(status_code=202, content=result)
    return result


@router.post("/weeks/{iso_week}")
def generate(
    iso_week: str, request: Request, regenerate: bool = False,
    engine: Engine = _engine, llm: LlmClient = _llm, user: CurrentUser = _user,
) -> Any:
    if (bad := _bad_week(iso_week)) is not None:
        return bad
    try:
        result = service.generate_review(
            engine, llm, _settings(request), user.oid, iso_week, regenerate, datetime.now(UTC)
        )
    except service.RegenerateLimit as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    return _out(result)


@router.get("/weeks/{iso_week}")
def read(iso_week: str, request: Request, engine: Engine = _engine) -> Any:
    if (bad := _bad_week(iso_week)) is not None:
        return bad
    result = service.get_review(engine, _settings(request), iso_week)
    if result is None:
        return JSONResponse(status_code=404, content={"detail": "no review for this week"})
    return _out(result)


class FeedbackIn(BaseModel):
    suggestion_index: int
    value: str


@router.post("/reviews/{review_id}/feedback", status_code=201)
def feedback(
    review_id: int, body: FeedbackIn, engine: Engine = _engine, user: CurrentUser = _user
) -> Any:
    try:
        return service.record_feedback(
            engine, user.oid, review_id, body.suggestion_index, body.value
        )
    except service.ReviewNotFound:
        return JSONResponse(status_code=404, content={"detail": "review not found"})
    except service.FeedbackInvalid as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})


@router.get("/ops")
def ops(month: str, request: Request, engine: Engine = _engine) -> Any:
    m = _MONTH.match(month)
    try:
        if m is None:
            raise ValueError
        start = date(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return JSONResponse(status_code=422, content={"detail": "month must be YYYY-MM"})
    with engine.connect() as conn:
        return service.ops_stats(conn, start, _settings(request).coach_monthly_cap_eur)
