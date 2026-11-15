"""Meal template detail route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Connection

from app.api.auth import get_current_user
from app.api.routes_products import get_connection
from app.db.engine import json_dumps
from app.services.templates import get_template

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/meal-templates/{template_id}")
def template_endpoint(
    template_id: int, conn: Annotated[Connection, Depends(get_connection)]
) -> Response:
    data = get_template(conn, template_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(content=json_dumps(data), media_type="application/json")
