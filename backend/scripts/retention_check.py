"""Report retention breaches and missing or stale live backups."""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from blobstore import AzureBlobStore, BlobStore


def check_retention(
    store: BlobStore, max_age_days: int = 25, *, max_fresh_hours: int = 48,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=max_age_days)
    blobs = store.list_all(include_deleted=True)
    offending = [blob.name for blob in blobs if blob.created < cutoff]
    for name in offending:
        print(name)
    live = [blob for blob in blobs if not blob.deleted]
    if not live:
        print("No live blob: no backup is available")
        return 1
    newest = max(live, key=lambda blob: blob.created)
    if newest.created < now - timedelta(hours=max_fresh_hours):
        print(f"Newest live blob {newest.name} is older than {max_fresh_hours} hours")
        return 1
    return int(bool(offending))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account_url")
    parser.add_argument("--max-age-days", type=int, default=25)
    parser.add_argument("--max-fresh-hours", type=int, default=48)
    args = parser.parse_args(argv)
    return check_retention(
        AzureBlobStore(args.account_url), args.max_age_days, max_fresh_hours=args.max_fresh_hours
    )


if __name__ == "__main__":
    sys.exit(main())
