"""Export, generation-based deletion, backup and restore (Task 15; A22, A31, A32).

Backup/restore tests need ``pg_dump``/``pg_restore``: on PATH, or in ``PG_BIN_DIR``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import routes_meals, routes_privacy
from app.api.auth import CurrentUser, get_current_user
from app.config import Settings
from app.db import catalogue
from app.db.engine import make_engine
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Nutrient, NutrientValue, Source
from app.main import create_app
from app.services import privacy

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
OID = "test-owner"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_script_{name}", BACKEND / "scripts" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


@pytest.fixture(scope="module")
def db_name():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    name = _create_db("test_privacy")
    url = _db_url(name)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
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


def _make_client(engine, ledger: Path) -> TestClient:
    app = create_app(Settings(deletion_ledger_path=ledger))
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(oid=OID)
    app.dependency_overrides[routes_meals.get_db_engine] = lambda: engine
    app.dependency_overrides[routes_privacy.get_db_engine] = lambda: engine
    return TestClient(app)


@pytest.fixture
def ledger(tmp_path) -> Path:
    d = tmp_path / "ledger"
    d.mkdir()
    (d / privacy.LEDGER_MARKER).touch()
    return d / "deletion_ledger.jsonl"


@pytest.fixture
def client(engine, ledger):
    try:
        yield _make_client(engine, ledger)
    finally:
        with engine.begin() as c:
            c.execute(
                sa.text(
                    "TRUNCATE meal_item, meal, day, idempotency_key, product_state, "
                    "nutrient_override, nutrient_value, profile_revision, source_record, "
                    "portion, weight_entry, profile, saved_meal_item, saved_meal, alias, "
                    "product, active_release, catalogue_release, user_account "
                    "RESTART IDENTITY CASCADE"
                )
            )


def _p(v: str, source: Source = Source.CIQUAL) -> NutrientValue:
    return NutrientValue.point(Decimal(v), source)


def _catalogue_product(engine, name: str) -> int:
    with engine.begin() as c:
        pid = catalogue.insert_product(
            c,
            owner="catalogue",
            kind="product",
            name=name,
            brand=None,
            basis_unit=Unit.G,
            state="as_sold",
        )
        rev = catalogue.add_source_revision(
            c, pid, Source.CIQUAL, name, {}, {Nutrient.SUGARS: _p("10")}
        )
        release = catalogue.create_release(c, f"release {name}")
        catalogue.set_product_state(
            c, release, pid, ProductState(Status.ACTIVE), rev.live_revision_id
        )
        catalogue.activate_release(c, release)
    return pid


def _user_product(engine, name: str, generation: int = 1) -> int:
    with engine.begin() as c:
        pid = catalogue.insert_product(
            c,
            owner="user",
            kind="product",
            name=name,
            brand=None,
            basis_unit=Unit.G,
            state="as_sold",
            data_generation=generation,
        )
        # Sugars known; fibre explicitly unknown (must stay null, never 0).
        rev = catalogue.add_source_revision(
            c,
            pid,
            Source.MANUAL,
            "m",
            {},
            {
                Nutrient.SUGARS: _p("5", Source.MANUAL),
                Nutrient.FIBRE: NutrientValue.unknown(Source.MANUAL),
            },
        )
        catalogue.set_product_state(c, None, pid, ProductState(Status.ACTIVE), rev.live_revision_id)
    return pid


def _meal(client, day: str = "2026-10-12") -> int:
    r = client.post("/meals", json={"day": day, "meal_type": "lunch"})
    assert r.status_code == 201, r.text
    return r.json()["meal_id"]


def _add(client, meal_id: int, product_id: int, amount: str = "100"):
    return client.post(
        f"/meals/{meal_id}/items",
        json={
            "product_id": product_id,
            "amount": amount,
            "unit": "g",
            "quantity_source": "weighed",
        },
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )


def _log(client, product_id: int, day: str, amount: str = "100") -> int:
    r = _add(client, _meal(client, day), product_id, amount)
    assert r.status_code == 201, r.text
    return r.json()["item_id"]


def _count(engine, sql: str) -> int:
    with engine.connect() as c:
        return c.execute(sa.text(sql)).scalar_one()


def test_export_contains_logged_items(client, engine):
    upid = _user_product(engine, "Mon yaourt")
    item = _log(client, upid, "2026-10-12", "125.5")
    r = client.get("/export")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    items = data["meal_items"]
    assert [i["id"] for i in items] == [item]
    assert items[0]["qty_value"] == "125.5"  # Decimal as string
    assert [p["name"] for p in data["products"]] == ["Mon yaourt"]
    fibre = [v for v in data["nutrient_values"] if v["nutrient"] == "fibre"]
    assert fibre and fibre[0]["known"] is False
    assert fibre[0]["lower"] is None and fibre[0]["upper"] is None  # unknown is never 0
    assert items[0]["nutrients"]["fibre"]["lower"] is None
    for key in (
        "meals",
        "days",
        "weights",
        "profile",
        "saved_meals",
        "aliases",
        "nutrient_overrides",
        "profile_revisions",
    ):
        assert key in data


def test_delete_removes_user_rows_keeps_catalogue(client, engine, ledger):
    cpid = _catalogue_product(engine, "Pomme")
    upid = _user_product(engine, "Mon yaourt")
    _log(client, cpid, "2026-10-12")
    _log(client, upid, "2026-10-12")
    with engine.begin() as c:
        rev = c.execute(
            sa.text("SELECT id FROM profile_revision WHERE product_id = :p"), {"p": upid}
        ).scalar_one()
        c.execute(
            sa.text(
                "INSERT INTO nutrient_override (product_id, nutrient, known, lower, upper, qualifier, "
                "source, derivation, verification, pinned_revision_id, reason, data_generation) "
                "VALUES (:p, 'sugars', true, 4, 4, 'none', 'manual', 'direct', 'user_verified', "
                ":r, 'label', 1)"
            ),
            {"p": upid, "r": rev},
        )
        c.execute(
            sa.text(
                "INSERT INTO weight_entry (day, kg, data_generation) VALUES ('2026-10-12', 80, 1)"
            )
        )
        c.execute(
            sa.text("INSERT INTO alias (text, product_id, data_generation) VALUES ('yo', :p, 1)"),
            {"p": upid},
        )
        sm = c.execute(
            sa.text("INSERT INTO saved_meal (name, data_generation) VALUES ('b', 1) RETURNING id")
        ).scalar_one()
        c.execute(
            sa.text(
                "INSERT INTO saved_meal_item (saved_meal_id, product_id, amount, unit) VALUES (:s, :p, 1, 'g')"
            ),
            {"s": sm, "p": cpid},
        )

    r = client.delete("/me/data")
    assert r.status_code == 200, r.text
    assert r.json()["generation"] == 2

    for table in (
        "meal_item",
        "meal",
        "day",
        "idempotency_key",
        "weight_entry",
        "alias",
        "saved_meal",
        "saved_meal_item",
        "nutrient_override",
        "profile",
    ):
        assert _count(engine, f"SELECT count(*) FROM {table}") == 0, table
    assert _count(engine, "SELECT count(*) FROM product WHERE owner = 'user'") == 0
    assert _count(engine, f"SELECT count(*) FROM profile_revision WHERE product_id = {upid}") == 0
    assert _count(engine, "SELECT count(*) FROM product_state WHERE release_id IS NULL") == 0
    # Catalogue survives.
    assert _count(engine, f"SELECT count(*) FROM product WHERE id = {cpid}") == 1
    assert _count(engine, f"SELECT count(*) FROM product_state WHERE product_id = {cpid}") == 1
    assert privacy.ledger_max_generation(ledger) == 2
    # Logging still works after deletion, at the new generation.
    _log(client, cpid, "2026-10-13")
    assert _count(engine, "SELECT min(data_generation) FROM meal_item") == 2


def test_a31_export_racing_delete_discarded(client, engine, monkeypatch, ledger):
    cpid = _catalogue_product(engine, "Pomme")
    _log(client, cpid, "2026-10-12")
    real_build = privacy.build_export

    def racing_build(conn):
        out = real_build(conn)
        privacy.delete_all_data(engine, OID, ledger)  # a deletion lands mid-export
        return out

    monkeypatch.setattr(privacy, "build_export", racing_build)
    r = client.get("/export")
    assert r.status_code == 409
    assert "meal_item" not in r.text


def test_stale_write_rejected(client, engine, monkeypatch):
    cpid = _catalogue_product(engine, "Pomme")
    meal = _meal(client)
    started, proceed = threading.Event(), threading.Event()
    real_require = privacy.require_generation

    def slow_require(conn, oid):
        # The request records its generation at start, then is held while a deletion lands.
        with engine.connect() as c:
            at_start = privacy.current_generation(c, oid)
        started.set()
        proceed.wait(10)
        return real_require(conn, oid, expected=at_start)

    monkeypatch.setattr(routes_meals, "require_generation", slow_require)
    result: list = []
    t = threading.Thread(target=lambda: result.append(_add(client, meal, cpid)))
    t.start()
    assert started.wait(10)
    monkeypatch.setattr(routes_meals, "require_generation", real_require)
    assert client.delete("/me/data").status_code == 200
    proceed.set()
    t.join(10)
    assert result[0].status_code == 409
    assert _count(engine, "SELECT count(*) FROM meal_item") == 0


def test_require_generation_blocks_concurrent_delete_until_commit(client, engine, ledger):
    """A write already inside its transaction is serialised before the deletion and purged."""
    cpid = _catalogue_product(engine, "Pomme")
    _meal(client)
    done = threading.Event()
    with engine.begin() as conn:
        g = privacy.require_generation(conn, OID)
        conn.execute(
            sa.text(
                "INSERT INTO meal (day, meal_type, data_generation) VALUES ('2026-10-14', 'dinner', :g)"
            ),
            {"g": g},
        )
        t = threading.Thread(
            target=lambda: (privacy.delete_all_data(engine, OID, ledger), done.set())
        )
        t.start()
        assert not done.wait(0.5)  # the deletion waits for this write to commit
    t.join(10)
    assert done.is_set()
    assert _count(engine, "SELECT count(*) FROM meal") == 0
    assert cpid


# --- backup / restore ---------------------------------------------------------------------


@pytest.fixture
def pg_bin():
    bin_dir = os.environ.get("PG_BIN_DIR")
    exe = "pg_dump.exe" if os.name == "nt" else "pg_dump"
    if bin_dir and (Path(bin_dir) / exe).exists():
        return bin_dir
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return None
    pytest.fail("pg_dump/pg_restore not found: set PG_BIN_DIR or put them on PATH")


@pytest.fixture
def scratch_dbs():
    names: list[str] = []
    yield names
    for n in names:
        _drop_db(n)


def _items_on(url: str, day: str) -> list[str]:
    eng = make_engine(url)
    try:
        with eng.connect() as c:
            return list(
                c.execute(
                    sa.text(
                        "SELECT p.name FROM meal_item i JOIN meal m ON m.id = i.meal_id "
                        "JOIN product p ON p.id = i.resolved_product_id WHERE m.day = :d ORDER BY i.id"
                    ),
                    {"d": day},
                ).scalars()
            )
    finally:
        eng.dispose()


def test_a22_a32_restore_reapplies_deletion_keeps_new_data(
    client, engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs
):
    backup, restore = _load_script("backup"), _load_script("restore")
    upid_a = _user_product(engine, "Produit A")
    _log(client, upid_a, "2026-10-12")
    dump1 = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    assert dump1.name.startswith("foodcoach-") and dump1.suffix == ".dump"

    assert client.delete("/me/data").status_code == 200
    cpid = _catalogue_product(engine, "Produit B")
    _log(client, cpid, "2026-10-13")
    dump2 = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    assert dump2 != dump1

    for dump, expect_b in ((dump1, False), (dump2, True)):
        target = _create_db("test_restore")
        scratch_dbs.append(target)
        counts = restore.restore(dump, _db_url(target), ledger, pg_bin)
        url = _db_url(target)
        assert _items_on(url, "2026-10-12") == []  # A never comes back
        assert _items_on(url, "2026-10-13") == (["Produit B"] if expect_b else [])
        eng = make_engine(url)
        with eng.connect() as c:
            assert (
                c.execute(sa.text("SELECT count(*) FROM product WHERE owner='user'")).scalar_one()
                == 0
            )
            assert c.execute(sa.text("SELECT data_generation FROM user_account")).scalar_one() == 2
            assert c.execute(
                sa.text("SELECT count(*) FROM product WHERE owner='catalogue'")
            ).scalar_one() == int(expect_b)
        eng.dispose()
        if not expect_b:
            assert counts["deleted"]["meal_item"] == 1 and counts["deleted"]["product"] == 1
        assert not any(counts["verification"].values())


def test_restore_into_fresh_db_logger_works(
    client, engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs
):
    backup, restore = _load_script("backup"), _load_script("restore")
    _log(client, _user_product(engine, "Produit A"), "2026-10-12")
    assert client.delete("/me/data").status_code == 200
    cpid = _catalogue_product(engine, "Produit B")
    _log(client, cpid, "2026-10-13", "150")
    dump2 = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)

    target = _create_db("test_fresh")
    scratch_dbs.append(target)
    restore.restore(dump2, _db_url(target), ledger, pg_bin)

    fresh = make_engine(_db_url(target))
    try:
        c2 = _make_client(fresh, ledger)
        r = c2.get("/days/2026-10-13")
        assert r.status_code == 200, r.text
        items = [i for m in r.json()["meals"] for i in m["items"]]
        assert [i["name"] for i in items] == ["Produit B"]
        assert c2.get("/days/2026-10-12").json()["meals"] == []
        # And the restored logger accepts new writes.
        _log(c2, cpid, "2026-10-14")
    finally:
        fresh.dispose()


def test_backup_cli_prints_path(db_name, tmp_path, pg_bin, monkeypatch, capsys):
    backup = _load_script("backup")
    monkeypatch.setenv("DATABASE_URL", _db_url(db_name))
    if pg_bin:
        monkeypatch.setenv("PG_BIN_DIR", pg_bin)
    backup.main(["--out", str(tmp_path / "cli")])
    printed = Path(capsys.readouterr().out.strip())
    assert printed.exists() and printed.parent == tmp_path / "cli"


# --- fix round 1 --------------------------------------------------------------------------


def test_ledger_written_ahead_survives_failed_commit(
    client, engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs, monkeypatch
):
    backup, restore = _load_script("backup"), _load_script("restore")
    _log(client, _user_product(engine, "Produit A"), "2026-10-12")

    def crash(conn, g):
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(privacy, "purge_below", crash)
    with pytest.raises(RuntimeError):
        privacy.delete_all_data(engine, OID, ledger)
    monkeypatch.undo()
    # The commit never happened: the DB still has generation 1 and the data...
    assert _count(engine, "SELECT data_generation FROM user_account") == 1
    assert _count(engine, "SELECT count(*) FROM meal_item") == 1
    # ...but the fsynced ledger already says 2, so a restore purges it.
    assert privacy.ledger_max_generation(ledger) == 2
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    target = _create_db("test_crash")
    scratch_dbs.append(target)
    restore.restore(dump, _db_url(target), ledger, pg_bin)
    assert _items_on(_db_url(target), "2026-10-12") == []


def test_restore_fails_without_ledger_unless_no_ledger(
    client, engine, db_name, tmp_path, pg_bin, scratch_dbs
):
    backup, restore = _load_script("backup"), _load_script("restore")
    _log(client, _catalogue_product(engine, "Pomme"), "2026-10-12")
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    target = _create_db("test_noledger")
    scratch_dbs.append(target)
    missing = tmp_path / "nope.jsonl"
    with pytest.raises(restore.RestoreError):
        restore.restore(dump, _db_url(target), missing, pg_bin)
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(restore.RestoreError):
        restore.restore(dump, _db_url(target), bad, pg_bin)
    args = [str(dump), _db_url(target), "--ledger", str(missing)]
    if pg_bin:
        os.environ["PG_BIN_DIR"] = pg_bin
    assert restore.main(args) == 1
    assert restore.main([str(dump), _db_url(target), "--no-ledger"]) == 0
    assert _items_on(_db_url(target), "2026-10-12") == ["Pomme"]


def test_restore_verification_fails_on_leftover_rows(
    client, engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs, monkeypatch
):
    backup, restore = _load_script("backup"), _load_script("restore")
    _log(client, _user_product(engine, "Produit A"), "2026-10-12")
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    privacy.append_ledger(ledger, 2)
    target = _create_db("test_verify")
    scratch_dbs.append(target)
    # A purge that silently misses meal items must be caught by verification.
    monkeypatch.setattr(restore, "purge_below", lambda conn, g: {})
    with pytest.raises(restore.RestoreError, match="meal_item_below"):
        restore.restore(dump, _db_url(target), ledger, pg_bin)
    monkeypatch.undo()
    result = restore.restore(dump, _db_url(target), ledger, pg_bin)
    assert result["verification"] and not any(result["verification"].values())


def test_restore_creates_missing_account_at_ledger_generation(
    engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs
):
    backup, restore = _load_script("backup"), _load_script("restore")
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)  # no account row yet
    privacy.append_ledger(ledger, 3)
    target = _create_db("test_noaccount")
    scratch_dbs.append(target)
    restore.restore(dump, _db_url(target), ledger, pg_bin, oid="someone")
    eng = make_engine(_db_url(target))
    try:
        with eng.connect() as c:
            row = c.execute(sa.text("SELECT entra_oid, data_generation FROM user_account")).one()
        assert tuple(row) == ("someone", 3)
    finally:
        eng.dispose()


def test_a40_restore_purges_coach_tables_below_ledger_generation(
    client, engine, db_name, ledger, tmp_path, pg_bin, scratch_dbs
):
    backup, restore = _load_script("backup"), _load_script("restore")
    _log(client, _user_product(engine, "Produit A"), "2026-10-12")
    with engine.begin() as c:
        rid = c.execute(sa.text(
            "INSERT INTO coach_review (iso_week, fact_sheet_hash, generation_no, fact_sheet, "
            "suggestions, source, fallback_reason, prompt_version, data_generation) VALUES "
            "('2026-W42', 'h', 1, '{}'::jsonb, '[{\"title\": \"t\"}]'::jsonb, 'template', "
            "'none', 'v2', 1) RETURNING id"
        )).scalar_one()
        c.execute(sa.text(
            "INSERT INTO llm_call (review_id, model, deployment, prompt_version, input_tokens, "
            "output_tokens, cost_eur, latency_ms, validation_ok, attempt, data_generation) "
            "VALUES (:r, 'm', 'd', 'v2', 1, 1, 0.001, 10, true, 1, 1)"
        ), {"r": rid})
        c.execute(sa.text(
            "INSERT INTO coach_feedback (review_id, suggestion_index, value, data_generation) "
            "VALUES (:r, 0, 'up', 1)"
        ), {"r": rid})
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    assert client.delete("/me/data").status_code == 200

    target = _create_db("test_coach_restore")
    scratch_dbs.append(target)
    counts = restore.restore(dump, _db_url(target), ledger, pg_bin)
    assert counts["deleted"]["coach_review"] == 1
    assert counts["deleted"]["llm_call"] == 1
    assert counts["deleted"]["coach_feedback"] == 1
    eng = make_engine(_db_url(target))
    try:
        with eng.connect() as c:
            for table in ("coach_review", "llm_call", "coach_feedback"):
                assert c.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    finally:
        eng.dispose()
    assert not any(counts["verification"].values())


def test_delete_refuses_without_marker(engine, tmp_path):
    with engine.begin() as c:
        privacy.require_generation(c, OID)
        before = privacy.current_generation(c, OID)
    try:
        with pytest.raises(privacy.LedgerVolumeError):
            privacy.delete_all_data(engine, OID, tmp_path / "deletion_ledger.jsonl")
        with engine.connect() as c:
            assert privacy.current_generation(c, OID) == before
        assert not (tmp_path / "deletion_ledger.jsonl").exists()
    finally:
        with engine.begin() as c:
            c.execute(sa.text("TRUNCATE user_account RESTART IDENTITY CASCADE"))


# --- restore re-applies the app role (deployment Task 3a, ruling R13) ---------------------

APP_ROLE = "foodcoach_app"


def _priv(url: str, role: str, table: str, priv: str) -> bool:
    eng = make_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(
                sa.text("SELECT has_table_privilege(:r, :t, :p)"),
                {"r": role, "t": table, "p": priv},
            ).scalar_one()
    finally:
        eng.dispose()


def test_restore_into_server_without_app_role(engine, db_name, tmp_path, pg_bin):
    """DR onto a server where foodcoach_app does not exist yet.

    Roles are cluster-wide, so the existing role is parked under another name (its grants
    in other databases follow it) and renamed back at teardown.
    """
    backup, restore = _load_script("backup"), _load_script("restore")
    dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
    parked = f"foodcoach_app_parked_{uuid.uuid4().hex[:8]}"
    target = _create_db("test_norole")
    admin = _admin()
    with admin.connect() as c:
        c.execute(sa.text(f"ALTER ROLE {APP_ROLE} RENAME TO {parked}"))
    try:
        restore.restore(dump, _db_url(target), None, pg_bin)  # raises on non-zero exit
        url = _db_url(target)
        assert _priv(url, APP_ROLE, "meal_item", "INSERT")
        assert not _priv(url, APP_ROLE, "alembic_version", "UPDATE")
        with admin.connect() as c:
            assert c.execute(
                sa.text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
            ).scalar_one() is False
    finally:
        _drop_db(target)
        with admin.connect() as c:
            c.execute(sa.text(f"DROP ROLE IF EXISTS {APP_ROLE}"))
            c.execute(sa.text(f"ALTER ROLE {parked} RENAME TO {APP_ROLE}"))
            c.execute(sa.text(f"ALTER ROLE {APP_ROLE} NOLOGIN PASSWORD NULL"))
        admin.dispose()


def test_restore_does_not_copy_source_acl(engine, db_name, tmp_path, pg_bin, scratch_dbs):
    backup, restore = _load_script("backup"), _load_script("restore")
    stranger = f"stranger_{uuid.uuid4().hex[:8]}"
    admin = _admin()
    with admin.connect() as c:
        c.execute(sa.text(f"CREATE ROLE {stranger} NOLOGIN"))
    try:
        with engine.begin() as c:
            c.execute(sa.text(f"GRANT SELECT ON meal_item TO {stranger}"))
        dump = backup.backup(_db_url(db_name), tmp_path / "b", pg_bin)
        target = _create_db("test_noacl")
        scratch_dbs.append(target)
        restore.restore(dump, _db_url(target), None, pg_bin)
        assert not _priv(_db_url(target), stranger, "meal_item", "SELECT")
        assert _priv(_db_url(target), APP_ROLE, "meal_item", "SELECT")
    finally:
        with engine.begin() as c:
            c.execute(sa.text(f"REVOKE ALL ON meal_item FROM {stranger}"))
        for name in scratch_dbs:
            _drop_db(name)
        scratch_dbs.clear()
        with admin.connect() as c:
            c.execute(sa.text(f"DROP ROLE IF EXISTS {stranger}"))
        admin.dispose()


def test_export_includes_dish(client, engine):
    upid = _user_product(engine, "Mon yaourt")
    r = client.post(
        f"/meals/{_meal(client, '2026-10-12')}/items",
        json={
            "product_id": upid,
            "amount": "100",
            "unit": "g",
            "quantity_source": "weighed",
            "dish": "Tajine",
        },
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 201, r.text
    items = client.get("/export").json()["data"]["meal_items"]
    assert [i["dish"] for i in items] == ["Tajine"]
