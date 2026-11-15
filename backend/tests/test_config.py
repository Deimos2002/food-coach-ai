"""The deletion ledger path is absolute and independent of the working directory (F7)."""

from pathlib import Path

from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]


def test_default_ledger_path_is_backend_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("DELETION_LEDGER_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    path = Settings(_env_file=None).deletion_ledger_path
    assert path.is_absolute()
    assert path == BACKEND / "data" / "deletion_ledger.jsonl"


def test_relative_ledger_path_resolves_against_backend_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DELETION_LEDGER_PATH", "elsewhere/ledger.jsonl")
    path = Settings(_env_file=None).deletion_ledger_path
    assert path == BACKEND / "elsewhere" / "ledger.jsonl"


def test_absolute_ledger_path_kept(tmp_path):
    target = tmp_path / "l.jsonl"
    assert Settings(_env_file=None, deletion_ledger_path=target).deletion_ledger_path == target
