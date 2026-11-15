"""Migration 0005 (coach tables), guideline cards and privacy coverage (coach Task 3, A40)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import routes_privacy, routes_week
from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
from app.db.engine import make_engine
from app.main import create_app
from app.services import privacy

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
OID = "test-owner"
COACH_TABLES = ("coach_review", "llm_call", "llm_budget", "coach_feedback", "guideline_card")


def _db_url(name: str) -> str:
    return (
        make_url(os.environ["TEST_DATABASE_URL"])
        .set(database=name)
        .render_as_string(hide_password=False)
    )


def _admin():
    return sa.create_engine(os.environ["TEST_DATABASE_URL"], isolation_level="AUTOCOMMIT")


def _create_db(prefix: str) -> str:
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin = _admin()
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return name


def _drop_db(name: str) -> None:
    admin = _admin()
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _cfg(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="module")
def db_name():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    name = _create_db("test_coach")
    command.upgrade(_cfg(_db_url(name)), "head")
    try:
        yield name
    finally:
        _drop_db(name)


@pytest.fixture(scope="module")
def engine(db_name):
    eng = make_engine(_db_url(db_name))
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def client(engine, tmp_path):
    (tmp_path / privacy.LEDGER_MARKER).touch()
    app = create_app(Settings(deletion_ledger_path=tmp_path / "ledger.jsonl"))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid=OID)
    app.dependency_overrides[routes_privacy.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_week.get_db_engine] = lambda: engine
    try:
        yield TestClient(app)
    finally:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "TRUNCATE coach_feedback, llm_call, coach_review, llm_budget, user_account "
                    "RESTART IDENTITY CASCADE"
                )
            )
            c.execute(sa.text("UPDATE guideline_card SET verified_by_user_at = NULL"))


def _tables(engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _count(engine, sql: str) -> int:
    with engine.connect() as c:
        return c.execute(sa.text(sql)).scalar_one()


def _seed_coach_rows(engine, generation: int = 1) -> None:
    with engine.begin() as c:
        rid = c.execute(
            sa.text(
                "INSERT INTO coach_review (iso_week, fact_sheet_hash, generation_no, fact_sheet, "
                "suggestions, source, fallback_reason, prompt_version, data_generation) VALUES "
                "('2026-W42', 'abc', 1, '{}'::jsonb, '[]'::jsonb, 'template', 'none', 'v1', :g) "
                "RETURNING id"
            ),
            {"g": generation},
        ).scalar_one()
        c.execute(
            sa.text(
                "INSERT INTO llm_call (review_id, model, deployment, prompt_version, "
                "input_tokens, output_tokens, cost_eur, latency_ms, validation_ok, attempt, "
                "data_generation) VALUES (:r, 'gpt', 'dep', 'v1', 10, 20, 0.0012, 300, true, 1, :g)"
            ),
            {"r": rid, "g": generation},
        )
        c.execute(
            sa.text(
                "INSERT INTO coach_feedback (review_id, suggestion_index, value, data_generation) "
                "VALUES (:r, 0, 'up', :g)"
            ),
            {"r": rid, "g": generation},
        )


def test_migration_roundtrip_0005(db_name):
    cfg = _cfg(_db_url(db_name))
    eng = sa.create_engine(_db_url(db_name))
    try:
        assert set(COACH_TABLES) <= _tables(eng)
        command.downgrade(cfg, "0004_body_saved")
        assert not (set(COACH_TABLES) & _tables(eng))
        command.upgrade(cfg, "0005_coach")
        assert set(COACH_TABLES) <= _tables(eng)
        with eng.connect() as c:
            assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0005_coach"
            )
    finally:
        eng.dispose()


def test_coach_review_unique_and_generation_no(engine, client):
    _seed_coach_rows(engine)
    insert = (
        "INSERT INTO coach_review (iso_week, fact_sheet_hash, generation_no, fact_sheet, "
        "suggestions, source, fallback_reason, prompt_version, data_generation) VALUES "
        "('2026-W42', 'abc', :n, '{}'::jsonb, '[]'::jsonb, 'template', 'none', 'v1', 1)"
    )
    for n in (1, 3):  # duplicate, then out of range
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
            c.execute(sa.text(insert), {"n": n})
    with engine.begin() as c:
        c.execute(sa.text(insert), {"n": 2})
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(
            sa.text("INSERT INTO llm_budget (period_start, cap_eur) VALUES ('2026-10-13', 2)")
        )


def test_seed_cards_unverified(client):
    r = client.get("/cards")
    assert r.status_code == 200, r.text
    cards = {c["id"]: c for c in r.json()}
    assert set(cards) == {"who_free_sugars", "who_salt", "efsa_protein_pri"}
    for card in cards.values():
        assert card["verified_by_user_at"] is None
        assert card["quote"] and card["url"].startswith("https://")
        assert card["retrieved_on"] == "2026-10-13"
    assert cards["who_free_sugars"]["threshold"] == "10"
    assert cards["who_salt"]["threshold"] == "5"
    assert cards["efsa_protein_pri"]["threshold"] == "0.83"
    assert "less than 10%" in cards["who_free_sugars"]["quote"]
    assert "5 g" in cards["who_salt"]["quote"]
    assert "0.83 g protein/kg body weight per day" in cards["efsa_protein_pri"]["quote"]


def test_verify_card_sets_timestamp(client):
    r = client.post("/cards/who_salt/verify")
    assert r.status_code == 200, r.text
    assert r.json()["verified_by_user_at"] is not None
    listed = {c["id"]: c for c in client.get("/cards").json()}
    assert listed["who_salt"]["verified_by_user_at"] is not None
    assert listed["who_free_sugars"]["verified_by_user_at"] is None
    r = client.post("/cards/who_salt/unverify")
    assert r.status_code == 200
    assert r.json()["verified_by_user_at"] is None
    r = client.post("/cards/nope/verify")
    assert r.status_code == 404
    assert "detail" in r.json()


def test_delete_all_purges_coach_tables(client, engine):
    _seed_coach_rows(engine)
    with engine.begin() as c:
        c.execute(
            sa.text("INSERT INTO llm_budget (period_start, cap_eur) VALUES ('2026-10-01', 2)")
        )
        c.execute(sa.text("UPDATE guideline_card SET verified_by_user_at = now()"))
    r = client.delete("/me/data")
    assert r.status_code == 200, r.text
    for table in ("coach_review", "llm_call", "coach_feedback"):
        assert _count(engine, f"SELECT count(*) FROM {table}") == 0, table
    # Reference and operational data are not user data.
    assert _count(engine, "SELECT count(*) FROM guideline_card") == 3
    assert _count(engine, "SELECT count(*) FROM llm_budget") == 1
    with engine.connect() as c:
        checks = privacy.verify_below(c, r.json()["generation"])
    for key in ("coach_review_below", "llm_call_below", "coach_feedback_below"):
        assert checks[key] == 0
    assert all(v == 0 for v in checks.values()), checks


def test_export_includes_coach_tables(client, engine):
    _seed_coach_rows(engine)
    r = client.get("/export")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert [x["iso_week"] for x in data["coach_reviews"]] == ["2026-W42"]
    assert data["llm_calls"][0]["cost_eur"] == "0.0012"
    assert [x["value"] for x in data["coach_feedback"]] == ["up"]


_REVIEW = (
    "INSERT INTO coach_review (iso_week, fact_sheet_hash, generation_no, fact_sheet, "
    "suggestions, source, fallback_reason, prompt_version, data_generation) VALUES "
    "(:w, 'h', 1, '{}'::jsonb, '[]'::jsonb, :s, :f, 'v1', :g)"
)


@pytest.mark.parametrize(
    ("source", "reason", "gen"),
    [
        ("chatbot", None, 1),  # bad source
        ("llm", "budget", 1),  # llm review carries no fallback reason
        ("template", None, 1),  # template needs a reason ('none' if chosen on purpose)
        ("template", "timeout", 1),  # unknown reason
        ("template", "none", 0),  # data_generation must be >= 1
    ],
)
def test_coach_review_checks_reject(engine, client, source, reason, gen):
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(sa.text(_REVIEW), {"w": "2026-W40", "s": source, "f": reason, "g": gen})


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("llm", None),
        ("template", "budget"),
        ("template", "validation"),
        ("template", "llm_error"),
        ("template", "none"),
    ],
)
def test_coach_review_checks_accept(engine, client, source, reason):
    with engine.begin() as c:
        c.execute(sa.text(_REVIEW), {"w": f"W-{reason}", "s": source, "f": reason, "g": 1})


def test_feedback_and_llm_call_checks_reject(engine, client):
    _seed_coach_rows(engine)
    with engine.connect() as c:
        rid = c.execute(sa.text("SELECT id FROM coach_review")).scalar_one()
    bad = [
        (
            "INSERT INTO coach_feedback (review_id, suggestion_index, value, data_generation) "
            "VALUES (:r, 0, 'meh', 1)"
        ),
        (
            "INSERT INTO coach_feedback (review_id, suggestion_index, value, data_generation) "
            "VALUES (:r, 0, 'up', 0)"
        ),
        (
            "INSERT INTO llm_call (review_id, model, deployment, prompt_version, attempt, "
            "data_generation) VALUES (:r, 'm', 'd', 'v1', 1, 0)"
        ),
    ]
    for sql in bad:
        with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
            c.execute(sa.text(sql), {"r": rid})
