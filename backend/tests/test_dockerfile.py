import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_two_stages_node_then_python():
    froms = re.findall(r"^FROM\s+(\S+)", _dockerfile(), re.MULTILINE)
    assert len(froms) == 2
    assert froms[0].startswith("node:20")
    assert froms[1].startswith("python:3.12-slim")


def test_runs_as_non_root_user():
    users = re.findall(r"^USER\s+(\S+)", _dockerfile(), re.MULTILINE)
    assert users
    assert users[-1] not in {"root", "0", "0:0"}


def test_installs_postgresql_client_16():
    assert "postgresql-client-16" in _dockerfile()


def test_copies_web_dist_to_static():
    assert re.search(
        r"^COPY\s+--from=\S+\s+\S*web/dist/?\s+/app/static/?\s*$", _dockerfile(), re.MULTILINE
    )


def test_dockerignore_excludes_local_state():
    lines = {
        line.strip().strip("/").removeprefix("**/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    }
    for entry in [".venv", "node_modules", ".env", "backend/data", "backups", ".worktrees", ".git",
                  ".env.*", "backend/evals/coach/recorded_runs", "backend/evals/coach/runs",
                  "backend/tests"]:
        assert entry in lines, entry


def test_runtime_stage_bakes_git_sha():
    runtime = _dockerfile().split("FROM python:")[1]
    assert re.search(r"^ARG\s+GIT_SHA", runtime, re.MULTILINE)
    assert re.search(r"^ENV\s+GIT_SHA=\$GIT_SHA\s*$", runtime, re.MULTILINE)
