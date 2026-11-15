"""The deletion ledger must live on its mounted volume (spec §3.5, §5.2)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.config import Settings
from app.main import create_app
from app.services.privacy import (
    LEDGER_MARKER,
    LedgerVolumeError,
    append_ledger,
    check_ledger_volume,
)


def _volume(tmp_path: Path) -> Path:
    d = tmp_path / "vol"
    d.mkdir()
    (d / LEDGER_MARKER).touch()
    return d


def test_check_passes_with_marker(tmp_path):
    check_ledger_volume(_volume(tmp_path) / "ledger.jsonl")


def test_check_fails_missing_dir(tmp_path):
    with pytest.raises(LedgerVolumeError):
        check_ledger_volume(tmp_path / "missing" / "ledger.jsonl")


def test_check_fails_without_marker(tmp_path):
    with pytest.raises(LedgerVolumeError):
        check_ledger_volume(tmp_path / "ledger.jsonl")


def test_check_fails_unwritable(tmp_path):
    d = _volume(tmp_path)
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(d, os.W_OK):
            pytest.skip("chmod has no effect here (Windows or root)")
        with pytest.raises(LedgerVolumeError):
            check_ledger_volume(d / "ledger.jsonl")
    finally:
        d.chmod(stat.S_IRWXU)


def test_append_does_not_create_dirs(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_ledger(tmp_path / "missing" / "ledger.jsonl", 2)
    assert list(tmp_path.iterdir()) == []


def test_startup_fails_without_marker(tmp_path):
    with pytest.raises(LedgerVolumeError):
        create_app(
            Settings(
                _env_file=None, require_ledger_volume=True, deletion_ledger_path=tmp_path / "l.jsonl"
            )
        )


def test_startup_passes_with_marker(tmp_path):
    create_app(
        Settings(
            _env_file=None,
            require_ledger_volume=True,
            deletion_ledger_path=_volume(tmp_path) / "l.jsonl",
        )
    )
