"""Back up the database: ``python scripts/backup.py [--out DIR] [--db-url URL]``.

Runs ``pg_dump --format=custom`` into ``DIR/foodcoach-<UTC timestamp>.dump`` (default
``backups/``, which is gitignored) and prints the path. ``pg_dump`` is taken from
``PG_BIN_DIR`` or PATH.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _pg import libpq_target, pg_tool
from blobstore import AzureBlobStore, BlobStore, dump_name

from app.config import Settings


def backup(db_url: str, out_dir: Path, bin_dir: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = out_dir / f"foodcoach-{stamp}.dump"
    dbname, env = libpq_target(db_url)
    subprocess.run(
        [
            pg_tool("pg_dump", bin_dir),
            "--format=custom",
            f"--file={path}",
            f"--dbname={dbname}",
        ],
        check=True,
        env=env,
    )
    return path


def backup_to_blob(
    db_url: str, store: BlobStore, sha: str, *, now: datetime | None = None,
    bin_dir: str | None = None,
) -> str:
    name = dump_name(now or datetime.now(UTC), sha)
    with TemporaryDirectory() as directory:
        path = backup(db_url, Path(directory), bin_dir=bin_dir)
        store.upload(name, path)
    return name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("backups"))
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--blob", metavar="ACCOUNT_URL")
    parser.add_argument("--container", default="backups")
    parser.add_argument(
        "--sha", default=os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA") or "unknown"
    )
    args = parser.parse_args(argv)
    url = args.db_url or Settings().database_url
    if args.blob:
        print(backup_to_blob(url, AzureBlobStore(args.blob, args.container), args.sha))
    else:
        print(backup(url, args.out))


if __name__ == "__main__":
    main()
