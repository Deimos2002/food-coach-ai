"""Dev auth: a local JWKS server and token minter standing in for Entra (Task 0).

``python -m scripts.dev_auth serve --port 8001`` serves ``/.well-known/jwks.json`` from
an RSA-2048 keypair kept in ``backend/data/dev_auth/`` (created on first use).

``python -m scripts.dev_auth token --oid dev-user [--hours 12]`` prints an RS256 JWT
with ``iss=http://localhost:8001/``, ``aud=api://foodcoach-dev`` and ``oid``.

Both commands refuse to run unless ``FOODCOACH_DEV_AUTH=1`` is set, so this never runs
against anything but a local dev setup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data" / "dev_auth"

ISSUER = "http://localhost:8001/"
AUDIENCE = "api://foodcoach-dev"
KEY_ID = "dev-key-1"


def _require_dev_auth() -> None:
    if os.environ.get("FOODCOACH_DEV_AUTH") != "1":
        raise RuntimeError(
            "refusing to run: set FOODCOACH_DEV_AUTH=1 to use the dev auth server/token"
        )


def load_or_create_keypair() -> rsa.RSAPrivateKey:
    """The RSA-2048 keypair in ``DATA_DIR``, creating it on first use."""
    _require_dev_auth()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_path = DATA_DIR / "private_key.pem"
    if key_path.exists():
        return serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(pem)
    return key


def build_jwks() -> dict:
    """The JWKS document for the dev keypair's public key."""
    key = load_or_create_keypair()
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = KEY_ID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def mint_token(oid: str, hours: int = 12) -> str:
    """An RS256 JWT for ``oid``, signed with the dev keypair."""
    key = load_or_create_keypair()
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "oid": oid,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KEY_ID})


class _JWKSHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/.well-known/jwks.json":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(build_jwks()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # quiet: this is a local dev-only server


def serve(port: int) -> None:
    _require_dev_auth()
    server = HTTPServer(("localhost", port), _JWKSHandler)
    print(f"Serving dev JWKS at http://localhost:{port}/.well-known/jwks.json")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.dev_auth")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="serve the dev JWKS endpoint")
    serve_parser.add_argument("--port", type=int, default=8001)

    token_parser = subparsers.add_parser("token", help="mint a dev RS256 JWT")
    token_parser.add_argument("--oid", required=True)
    token_parser.add_argument("--hours", type=int, default=12)

    args = parser.parse_args(argv)

    try:
        if args.command == "serve":
            serve(args.port)
        elif args.command == "token":
            print(mint_token(oid=args.oid, hours=args.hours))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
