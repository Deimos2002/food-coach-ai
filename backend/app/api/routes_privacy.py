"""Export and delete-all-my-data routes (Task 15). Every route requires the authenticated user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from app.api.auth import CurrentUser, get_current_user
from app.db.engine import get_engine
from app.services import privacy

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db_engine() -> Engine:
    return get_engine()


_user = Depends(get_current_user)
_engine = Depends(get_db_engine)


def stale_generation_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "stale data generation"})


@router.get("/export")
def export(user: CurrentUser = _user, engine: Engine = _engine):
    return privacy.export_data(engine, user.oid)


@router.delete("/me/data")
def delete_my_data(request: Request, user: CurrentUser = _user, engine: Engine = _engine):
    ledger = request.app.state.settings.deletion_ledger_path
    return privacy.delete_all_data(engine, user.oid, ledger)
