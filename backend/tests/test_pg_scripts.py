"""backup.py / restore.py pass the password via PGPASSWORD, never on the command line (F6)."""

import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
URL = "postgresql+psycopg://alice:s3cr%40t@db.example:5433/foodcoach"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_unit_script_{name}", BACKEND / "scripts" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(args, check, env=None, **kwargs):
        recorded.append((args, env))

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setenv("PGPASSWORD", "from-outer-env")
    return recorded


def _assert_no_password_in_args(args, env):
    assert not any("s3cr" in str(a) for a in args), args
    assert env is not None and env["PGPASSWORD"] == "s3cr@t"
    dbname = next(a for a in args if str(a).startswith("--dbname="))
    assert dbname == "--dbname=postgresql://alice@db.example:5433/foodcoach"


def test_backup_passes_password_in_env(calls, tmp_path):
    backup = _load_script("backup")
    (tmp_path / "pg_dump").touch()
    backup.backup(URL, tmp_path, bin_dir=str(tmp_path))
    ((args, env),) = calls
    _assert_no_password_in_args(args, env)


def test_restore_passes_password_in_env(calls, tmp_path, monkeypatch):
    restore = _load_script("restore")
    monkeypatch.setattr(restore, "apply_app_role", lambda url: None)
    (tmp_path / "pg_restore").touch()
    restore.restore(tmp_path / "x.dump", URL, None, bin_dir=str(tmp_path))
    ((args, env),) = calls
    _assert_no_password_in_args(args, env)


def test_backup_uploads_to_blob(monkeypatch, tmp_path, fake_blob_store):
    from datetime import UTC, datetime

    script = _load_script("backup")
    (tmp_path / "pg_dump").touch()

    def fake_dump(args, **kwargs):
        path = Path(next(arg.removeprefix("--file=") for arg in args if arg.startswith("--file=")))
        path.write_bytes(b"database dump")

    monkeypatch.setattr(script.subprocess, "run", fake_dump)
    store = fake_blob_store()
    name = script.backup_to_blob(
        URL, store, "abc123", now=datetime(2027, 1, 4, 2, 30, tzinfo=UTC),
        bin_dir=str(tmp_path),
    )
    assert name == "20270104T023000Z-abc123.dump"
    assert store.contents == {name: b"database dump"}
    assert not store.uploaded_paths[0].exists()


def test_restore_from_blob_downloads_then_restores(monkeypatch, tmp_path, fake_blob_store):
    script = _load_script("restore")
    store = fake_blob_store()
    store.contents["backup.dump"] = b"database dump"
    seen = []

    def fake_restore(path, target_url, ledger, **kwargs):
        assert path.read_bytes() == b"database dump"
        assert target_url == URL
        assert ledger == tmp_path / "ledger.jsonl"
        seen.append(path)
        return {"verified": True}

    monkeypatch.setattr(script, "restore", fake_restore)
    assert script.restore_from_blob(
        store, "backup.dump", URL, tmp_path / "ledger.jsonl"
    ) == {"verified": True}
    assert not seen[0].exists()


@pytest.mark.parametrize("blob", [False, True])
def test_restore_cli_sources(monkeypatch, tmp_path, blob):
    script = _load_script("restore")
    seen = []
    monkeypatch.setattr(script, "restore", lambda *a, **k: seen.append(a) or {})
    monkeypatch.setattr(script, "restore_from_blob", lambda *a, **k: seen.append(a) or {})
    monkeypatch.setattr(script, "AzureBlobStore", lambda url: "store")
    source = ["--blob", "backup.dump", "--account-url", "https://storage"] if blob else ["x.dump"]
    ledger = tmp_path / "ledger.jsonl"
    assert script.main([*source, URL, "--ledger", str(ledger)]) == 0
    expected = [("store", "backup.dump", URL, ledger)] if blob else [
        (Path("x.dump"), URL, ledger)
    ]
    assert seen == expected


@pytest.mark.parametrize("source", [[], ["x.dump", "--blob", "other"], ["--blob", "other"]])
def test_restore_cli_rejects_invalid_sources(source, monkeypatch):
    monkeypatch.delenv("BACKUP_ACCOUNT_URL", raising=False)
    script = _load_script("restore")
    with pytest.raises(SystemExit) as exc:
        script.main([*source, URL, "--no-ledger"])
    assert exc.value.code == 2


def test_restore_account_url_from_env(monkeypatch):
    script = _load_script("restore")
    monkeypatch.setenv("BACKUP_ACCOUNT_URL", "https://env-storage")
    accounts = []
    monkeypatch.setattr(script, "AzureBlobStore", lambda url: accounts.append(url) or "store")
    monkeypatch.setattr(script, "restore_from_blob", lambda *a, **k: {})
    assert script.main(["--blob", "backup.dump", URL, "--no-ledger"]) == 0
    assert accounts == ["https://env-storage"]


def test_restore_empty_account_url_fails(monkeypatch, capsys):
    script = _load_script("restore")
    monkeypatch.setenv("BACKUP_ACCOUNT_URL", "")
    with pytest.raises(SystemExit) as exc:
        script.main(["--blob", "backup.dump", URL, "--no-ledger"])
    assert exc.value.code == 2
    assert "--account-url is required with --blob" in capsys.readouterr().err


@pytest.mark.parametrize(
    "git_sha,github_sha,explicit,expected",
    [("image-sha", "github-sha", None, "image-sha"),
     ("image-sha", "github-sha", "cli-sha", "cli-sha"),
     (None, "github-sha", None, "github-sha"),
     (None, None, None, "unknown")],
)
def test_backup_sha_precedence(monkeypatch, git_sha, github_sha, explicit, expected):
    script = _load_script("backup")
    for key, value in [("GIT_SHA", git_sha), ("GITHUB_SHA", github_sha)]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    seen = []
    monkeypatch.setattr(script, "AzureBlobStore", lambda *a: "store")
    monkeypatch.setattr(script, "backup_to_blob", lambda url, store, sha: seen.append(sha))
    args = ["--blob", "https://storage", "--db-url", URL]
    if explicit:
        args.extend(["--sha", explicit])
    script.main(args)
    assert seen == [expected]
