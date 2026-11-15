from datetime import UTC, datetime, timedelta

import pytest
from blobstore import BlobInfo
from retention_check import check_retention


@pytest.mark.parametrize(
    "ages,deleted,expected", [([1, 20], False, 0), ([26], False, 1), ([26], True, 1)],
    ids=["retention_ok", "retention_flags_old", "retention_counts_soft_deleted"],
)
def test_retention(ages, deleted, expected, capsys, fake_blob_store):
    now = datetime(2027, 1, 30, tzinfo=UTC)
    store = fake_blob_store([
        BlobInfo(f"day-{age}.dump", now - timedelta(days=age), deleted) for age in ages
    ])
    assert check_retention(store, now=now) == expected
    output = capsys.readouterr().out
    assert ("day-26.dump" in output) == bool(expected)


@pytest.mark.parametrize("deleted_only", [False, True])
def test_retention_empty_fails(fake_blob_store, capsys, deleted_only):
    now = datetime(2027, 1, 30, tzinfo=UTC)
    blobs = [BlobInfo("deleted.dump", now, True)] if deleted_only else []
    assert check_retention(fake_blob_store(blobs), now=now) == 1
    assert "no live blob" in capsys.readouterr().out.lower()


def test_retention_stale_newest_fails(fake_blob_store, capsys):
    now = datetime(2027, 1, 30, tzinfo=UTC)
    store = fake_blob_store([
        BlobInfo("stale.dump", now - timedelta(hours=49), False),
        BlobInfo("older.dump", now - timedelta(days=5), False),
        BlobInfo("deleted.dump", now, True),
    ])
    assert check_retention(store, now=now) == 1
    output = capsys.readouterr().out.lower()
    assert "newest live blob" in output
    assert "stale.dump" in output


def test_retention_cli_max_fresh_hours(monkeypatch, fake_blob_store, capsys):
    from retention_check import main

    store = fake_blob_store([
        BlobInfo("recent.dump", datetime.now(UTC) - timedelta(hours=3), False),
    ])
    monkeypatch.setattr("retention_check.AzureBlobStore", lambda url: store)
    assert main(["https://storage"]) == 0
    assert main(["https://storage", "--max-fresh-hours", "2"]) == 1
    assert "older than 2 hours" in capsys.readouterr().out
