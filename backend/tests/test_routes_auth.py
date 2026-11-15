"""Every API route except /health needs a bearer token (A21, final fixes F4)."""

import re

from fastapi.testclient import TestClient

from app.api import (
    routes_body,
    routes_meals,
    routes_privacy,
    routes_products,
    routes_saved,
    routes_week,
)
from app.api.auth import get_current_user
from app.main import create_app

_SAMPLE = {"day": "2026-10-12"}


def _path(template: str) -> str:
    return re.sub(r"\{(\w+)(:\w+)?\}", lambda m: _SAMPLE.get(m.group(1), "1"), template)


def test_every_route_except_health_requires_token():
    app = create_app()
    client = TestClient(app)
    checked = 0
    # The OpenAPI schema lists every API route, including those of included routers.
    for path, operations in app.openapi()["paths"].items():
        if path == "/health":
            continue
        for method in operations:
            response = client.request(method.upper(), _path(path), json={})
            assert response.status_code == 401, (method, path, response.status_code)
            checked += 1
    assert checked >= 20


def test_every_router_requires_auth_at_router_level():
    for module in (
        routes_body,
        routes_meals,
        routes_privacy,
        routes_products,
        routes_saved,
        routes_week,
    ):
        calls = {d.dependency for d in module.router.dependencies}
        assert get_current_user in calls, module.__name__
