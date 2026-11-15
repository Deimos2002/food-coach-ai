"""Bearer token validation for the single permitted Entra user."""

from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings, get_settings

_bearer_dependency = Depends(HTTPBearer(auto_error=False))
_settings_dependency = Depends(get_settings)


@dataclass(frozen=True)
class CurrentUser:
    oid: str


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = _bearer_dependency,
    settings: Settings = _settings_dependency,
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not settings.allowed_oid or not settings.allowed_oid.strip():
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(credentials.credentials)
        required = ["iss", "aud", "exp", "oid"]
        if settings.allowed_tid:
            required += ["tid", "scp"]
        claims = jwt.decode(
            credentials.credentials,
            key.key,
            algorithms=["RS256"],
            issuer=settings.entra_issuer,
            audience=settings.entra_audience,
            options={"require": required},
            leeway=60,
        )
        if settings.allowed_tid:
            if claims["tid"] != settings.allowed_tid:
                raise ValueError("unexpected tid")
            scopes = claims["scp"]
            if not isinstance(scopes, str) or settings.required_scope not in scopes.split(" "):
                raise ValueError("missing scope")
        oid = claims.get("oid")
        if not oid or oid != settings.allowed_oid:
            raise ValueError("unexpected oid")
    except (jwt.PyJWTError, ValueError, TypeError, OSError) as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return CurrentUser(oid=claims["oid"])
