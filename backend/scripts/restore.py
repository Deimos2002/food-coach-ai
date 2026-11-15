"""Restore a backup, re-apply deletions and verify (spec A.8, A22, A32).

``python scripts/restore.py <dump> <target_db_url> [--ledger PATH | --no-ledger] [--oid OID]``

1. ``pg_restore --clean --if-exists --no-owner --no-acl`` into the target database, then
   re-apply the app role and its grants (``app.db.app_role``) as the restoring login.
2. Read the deletion ledger (a file outside the database; every line is authoritative,
   even one whose transaction never committed) and take its max generation G. A missing
   or unreadable ledger is an error unless ``--no-ledger`` is passed explicitly.
3. If G exceeds the restored ``user_account.data_generation``, delete every user row with
   ``data_generation < G`` and set the account generation to G. With no account row, one is
   created for ``--oid`` (default ``ALLOWED_OID``) at generation max(G, 1).
4. Verify: re-count rows below G and orphaned cascade children. Any non-zero count fails.
Prints the deleted counts and the verification summary as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from _pg import libpq_target, pg_tool
from blobstore import AzureBlobStore, BlobStore

from app.config import Settings
from app.db.app_role import app_role_statements
from app.services.privacy import ledger_max_generation, purge_below, verify_below


class RestoreError(Exception):
    pass


def apply_app_role(target_url: str) -> None:
    """Re-create the app role and its grants as the restoring login.

    pg_restore runs with ``--no-acl`` (ACLs name the source server's roles), so this is
    where the target gets its privileges; the restoring login owns the default privileges.
    """
    engine = sa.create_engine(target_url)
    try:
        with engine.begin() as conn:
            for statement in app_role_statements():
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()


def restore(
    dump: Path,
    target_url: str,
    ledger: Path | None,
    bin_dir: str | None = None,
    oid: str | None = None,
) -> dict:
    """``ledger=None`` means ``--no-ledger``. Raises ``RestoreError`` on failed verification."""
    g: int | None = None
    if ledger is not None:
        try:
            g = ledger_max_generation(ledger)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RestoreError(f"deletion ledger {ledger} missing or unreadable: {exc}") from exc
    dbname, env = libpq_target(target_url)
    subprocess.run(
        [
            pg_tool("pg_restore", bin_dir),
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            f"--dbname={dbname}",
            str(dump),
        ],
        check=True,
        env=env,
    )
    apply_app_role(target_url)
    result: dict = {"ledger_generation": g, "deleted": {}, "verification": {}}
    if g is None:
        return result
    engine = sa.create_engine(target_url)
    try:
        with engine.begin() as conn:
            restored = conn.execute(
                sa.text("SELECT max(data_generation) FROM user_account")
            ).scalar_one_or_none()
            if restored is None:
                if not oid:
                    raise RestoreError("no user_account row and no --oid / ALLOWED_OID")
                conn.execute(
                    sa.text(
                        "INSERT INTO user_account (entra_oid, data_generation) VALUES (:o, :g)"
                    ),
                    {"o": oid, "g": max(g, 1)},
                )
            if restored is None or restored < g:
                result["deleted"] = purge_below(conn, g)
                conn.execute(sa.text("UPDATE user_account SET data_generation = :g"), {"g": g})
            result["verification"] = verify_below(conn, g)
            if any(result["verification"].values()):
                raise RestoreError(f"verification failed: {result['verification']}")
        return result
    finally:
        engine.dispose()


def restore_from_blob(
    store: BlobStore, name: str, target_url: str, ledger: Path | None,
    bin_dir: str | None = None, oid: str | None = None,
) -> dict:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "backup.dump"
        store.download(name, path)
        return restore(path, target_url, ledger, bin_dir=bin_dir, oid=oid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("dump", type=Path, nargs="?")
    source.add_argument("--blob", metavar="NAME")
    parser.add_argument("--account-url", default=os.environ.get("BACKUP_ACCOUNT_URL"))
    parser.add_argument("target_db_url")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ledger", type=Path, default=None)
    group.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--oid", default=None)
    args = parser.parse_args(argv)
    if args.blob and not args.account_url:
        parser.error("--account-url is required with --blob")
    settings = Settings()
    ledger = None if args.no_ledger else (args.ledger or settings.deletion_ledger_path)
    try:
        if args.blob:
            result = restore_from_blob(
                AzureBlobStore(args.account_url), args.blob, args.target_db_url,
                ledger, oid=args.oid or settings.allowed_oid,
            )
        else:
            result = restore(
                args.dump, args.target_db_url, ledger, oid=args.oid or settings.allowed_oid
            )
    except RestoreError as exc:
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
