from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.auth import get_current_user
from app.config import Settings, get_settings
from app.main import create_app

_DROP = object()


def _make_auth(monkeypatch, allowed_tid):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        entra_issuer="https://issuer.example/",
        entra_audience="food-coach",
        allowed_oid="owner",
        jwks_url="https://issuer.example/keys",
        allowed_tid=allowed_tid,
    )
    monkeypatch.setattr(
        "app.api.auth.PyJWKClient.get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=key.public_key()),
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected", dependencies=[Depends(get_current_user)])
    def protected():
        return {"ok": True}

    def token(**changes):
        claims = {
            "iss": settings.entra_issuer,
            "aud": settings.entra_audience,
            "oid": "owner",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "tid": "tenant-1",
            "scp": "Food.ReadWrite",
        }
        claims.update(changes)
        claims = {k: v for k, v in claims.items() if v is not _DROP}
        return jwt.encode(claims, key, algorithm="RS256")

    return TestClient(app), token


@pytest.fixture
def auth(monkeypatch):
    return _make_auth(monkeypatch, "tenant-1")


def test_a21_no_token_401(auth):
    client, _ = auth
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"oid": "other"},
        {"exp": datetime(2020, 1, 1, tzinfo=UTC)},
        {"aud": "other"},
        {"iss": "https://other.example/"},
        {"exp": None},
    ],
)
def test_invalid_claims_401(auth, changes):
    client, token = auth
    response = client.get("/protected", headers={"Authorization": f"Bearer {token(**changes)}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_valid_token_200(auth):
    client, token = auth
    assert (
        client.get("/protected", headers={"Authorization": f"Bearer {token()}"}).status_code == 200
    )


def test_empty_allowed_oid_rejects_all(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        entra_issuer="https://issuer.example/",
        entra_audience="food-coach",
        allowed_oid="",
        jwks_url="https://issuer.example/keys",
    )
    monkeypatch.setattr(
        "app.api.auth.PyJWKClient.get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=key.public_key()),
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected", dependencies=[Depends(get_current_user)])
    def protected():
        return {"ok": True}

    client = TestClient(app)
    token = jwt.encode(
        {
            "iss": settings.entra_issuer,
            "aud": settings.entra_audience,
            "oid": "",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        key,
        algorithm="RS256",
    )
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_missing_oid_claim_rejected(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        entra_issuer="https://issuer.example/",
        entra_audience="food-coach",
        allowed_oid="owner",
        jwks_url="https://issuer.example/keys",
    )
    monkeypatch.setattr(
        "app.api.auth.PyJWKClient.get_signing_key_from_jwt",
        lambda self, token: SimpleNamespace(key=key.public_key()),
    )
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected", dependencies=[Depends(get_current_user)])
    def protected():
        return {"ok": True}

    client = TestClient(app)
    token = jwt.encode(
        {
            "iss": settings.entra_issuer,
            "aud": settings.entra_audience,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        key,
        algorithm="RS256",
    )
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_token_expired_30s_ago_accepted_within_leeway(auth):
    client, token = auth
    response = client.get(
        "/protected",
        headers={
            "Authorization": f"Bearer {token(exp=datetime.now(UTC) - timedelta(seconds=30))}"
        },
    )
    assert response.status_code == 200


def test_token_expired_120s_ago_rejected(auth):
    client, token = auth
    response = client.get(
        "/protected",
        headers={
            "Authorization": f"Bearer {token(exp=datetime.now(UTC) - timedelta(seconds=120))}"
        },
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_wrong_signature_401(auth):
    client, _ = auth
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "iss": "https://issuer.example/",
            "aud": "food-coach",
            "oid": "owner",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        other,
        algorithm="RS256",
    )
    assert client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def _get(client, token):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.get("/protected", headers=headers)


def _assert_401(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_missing_token(auth):
    client, _ = auth
    _assert_401(_get(client, None))


def test_malformed_token(auth):
    client, _ = auth
    _assert_401(_get(client, "abc"))


def test_expired_token(auth):
    client, token = auth
    _assert_401(_get(client, token(exp=datetime.now(UTC) - timedelta(minutes=10))))


def test_wrong_audience(auth):
    client, token = auth
    _assert_401(_get(client, token(aud="api://other-app")))


def test_wrong_issuer(auth):
    client, token = auth
    _assert_401(_get(client, token(iss="https://other.example/")))


def test_wrong_tenant(auth):
    client, token = auth
    _assert_401(_get(client, token(tid="tenant-2")))


def test_missing_tid(auth):
    client, token = auth
    _assert_401(_get(client, token(tid=_DROP)))


def test_wrong_user(auth):
    client, token = auth
    _assert_401(_get(client, token(oid="other")))


def test_missing_scp(auth):
    client, token = auth
    _assert_401(_get(client, token(scp=_DROP)))


def test_wrong_scope(auth):
    client, token = auth
    _assert_401(_get(client, token(scp="User.Read")))


@pytest.mark.parametrize("scp", ["Food.ReadWrite.All", "xFood.ReadWrite"])
def test_scp_substring_rejected(auth, scp):
    client, token = auth
    _assert_401(_get(client, token(scp=scp)))


def test_scope_among_several(auth):
    client, token = auth
    assert _get(client, token(scp="User.Read Food.ReadWrite")).status_code == 200


def test_valid_token(auth):
    client, token = auth
    assert _get(client, token()).status_code == 200


def test_tid_unchecked_when_unset(monkeypatch):
    client, token = _make_auth(monkeypatch, "")
    assert _get(client, token(tid=_DROP, scp=_DROP)).status_code == 200
