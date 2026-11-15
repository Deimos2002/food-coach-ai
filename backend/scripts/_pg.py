"""Shared helpers for the backup/restore scripts: locate PostgreSQL client tools."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sqlalchemy.engine import URL, make_url


def pg_tool(name: str, bin_dir: str | os.PathLike[str] | None = None) -> str:
    """Path to ``pg_dump``/``pg_restore``: ``bin_dir``, else ``$PG_BIN_DIR``, else PATH."""
    bin_dir = bin_dir or os.environ.get("PG_BIN_DIR")
    if bin_dir:
        for candidate in (Path(bin_dir) / f"{name}.exe", Path(bin_dir) / name):
            if candidate.exists():
                return str(candidate)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found: set PG_BIN_DIR or put it on PATH")
    return found


def libpq_target(sqlalchemy_url: str) -> tuple[str, dict[str, str]]:
    """``(dbname URL without the password, environment with PGPASSWORD)`` for a libpq tool.

    The password never appears on the command line (visible in process listings); it is
    passed through ``PGPASSWORD`` instead. Without a password in the URL the environment
    is passed through unchanged.
    """
    url = make_url(sqlalchemy_url).set(drivername="postgresql")
    env = dict(os.environ)
    if url.password is not None:
        env["PGPASSWORD"] = str(url.password)
    no_password = URL.create(
        "postgresql", url.username, None, url.host, url.port, url.database, url.query
    )
    return no_password.render_as_string(hide_password=False), env
