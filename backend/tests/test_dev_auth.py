"""Dev JWKS/token script (Task 0): refuses without FOODCOACH_DEV_AUTH, and a minted
token validates against the app's own ``get_current_user`` with the JWKS it builds."""

from __future__ import annotations

import pytest
from jwt import PyJWKClient

from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
from scripts import dev_auth


def test_dev_auth_refuses_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FOODCOACH_DEV_AUTH", raising=False)
    monkeypatch.setattr(dev_auth, "DATA_DIR", tmp_path)
    with pytest.raises(RuntimeError):
        dev_auth.load_or_create_keypair()
    with pytest.raises(RuntimeError):
        dev_auth.mint_token(oid="dev-user", hours=12)


def test_dev_token_validates_with_app_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("FOODCOACH_DEV_AUTH", "1")
    monkeypatch.setattr(dev_auth, "DATA_DIR", tmp_path)

    token = dev_auth.mint_token(oid="dev-user", hours=12)
    jwks = dev_auth.build_jwks()

    settings = Settings(
        entra_issuer="http://localhost:8001/",
        entra_audience="api://foodcoach-dev",
        allowed_oid="dev-user",
        jwks_url="http://localhost:8001/.well-known/jwks.json",
    )

    # PyJWKClient fetches over plain urllib, not httpx, so the JWKS fetch itself is
    # monkeypatched rather than routed through respx; the JWKS content is still the real
    # one ``dev_auth`` builds from the same keypair that signed the token.
    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda self: jwks)
    user = get_current_user(credentials=_bearer(token), settings=settings)
    assert isinstance(user, CurrentUser)
    assert user.oid == "dev-user"


def _bearer(token: str):
    from fastapi.security import HTTPAuthorizationCredentials

    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
