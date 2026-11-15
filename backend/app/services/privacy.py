"""Data generation, stale-write rejection, export and deletion (spec §7.4, A.8, Task 15).

- ``user_account.data_generation`` is bumped by ``DELETE /me/data`` in the same transaction
  that deletes every user row whose generation is below the new one.
- Every user write calls ``require_generation`` inside its transaction. It takes a
  ``FOR SHARE`` lock on the account row, so a concurrent deletion (an ``UPDATE`` of that row)
  waits until the write commits and then deletes what it wrote. A write that carries the
  generation it started with (``expected``) is rejected with ``StaleGeneration`` (409) if
  that generation is no longer current.
- After the deletion commits, one JSON line is appended to the deletion ledger, a file
  outside the database. ``scripts/restore.py`` replays it with ``purge_below``.
- Catalogue rows (``owner = 'catalogue'``) are not user data and are never touched.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.db.tables import user_account

LEDGER_MARKER = ".ledger-volume"


class LedgerVolumeError(RuntimeError):
    """The ledger directory is not the mounted ledger volume (spec §3.5)."""


def check_ledger_volume(path: Path) -> None:
    """Refuse a ledger whose directory is missing, unmarked or read-only.

    ``path`` is the ledger file; its parent must exist, hold ``LEDGER_MARKER`` (written once
    at bootstrap on the mounted share) and be writable. This keeps a missing mount from
    producing a silent local ledger.
    """
    d = path.parent
    if not d.is_dir():
        raise LedgerVolumeError(f"ledger directory {d} does not exist")
    if not (d / LEDGER_MARKER).is_file():
        raise LedgerVolumeError(f"ledger directory {d} has no {LEDGER_MARKER} marker")
    if not os.access(d, os.W_OK):
        raise LedgerVolumeError(f"ledger directory {d} is not writable")


class StaleGeneration(Exception):
    """A write or export started at a data generation that has since been deleted."""


def _ensure_account(conn: Connection, oid: str) -> None:
    conn.execute(
        pg_insert(user_account)
        .values(entra_oid=oid)
        .on_conflict_do_nothing(index_elements=[user_account.c.entra_oid])
    )


def current_generation(conn: Connection, oid: str) -> int:
    """The account's generation without locking (1 if the account does not exist yet)."""
    g = conn.execute(
        sa.select(user_account.c.data_generation).where(user_account.c.entra_oid == oid)
    ).scalar_one_or_none()
    return 1 if g is None else g


def require_generation(conn: Connection, oid: str, expected: int | None = None) -> int:
    """Lock the account row for this transaction and return its current generation.

    Raises ``StaleGeneration`` if ``expected`` is given and is no longer current.
    """
    _ensure_account(conn, oid)
    g = conn.execute(
        sa.select(user_account.c.data_generation)
        .where(user_account.c.entra_oid == oid)
        .with_for_update(read=True)
    ).scalar_one()
    if expected is not None and expected != g:
        raise StaleGeneration(f"generation {expected} is stale (current {g})")
    return g


# --- deletion ---------------------------------------------------------------------------

_DOOMED_PRODUCTS = "SELECT id FROM product WHERE owner = 'user' AND data_generation < :g"
_DOOMED_OVERRIDES = (
    "SELECT id FROM nutrient_override WHERE data_generation < :g "
    f"OR product_id IN ({_DOOMED_PRODUCTS})"
)

# Order matters: meal items reference products (no cascade) and override ids (an array, no
# FK), so they go first. Idempotency keys carry no generation; they only cache responses
# of writes made before the deletion, so they are all dropped.
_DOOMED_REVIEWS = "SELECT id FROM coach_review WHERE data_generation < :g"

_PURGE: list[tuple[str, str]] = [
    (
        "coach_feedback",
        f"DELETE FROM coach_feedback WHERE data_generation < :g OR review_id IN ({_DOOMED_REVIEWS})",
    ),
    (
        "llm_call",
        f"DELETE FROM llm_call WHERE data_generation < :g OR review_id IN ({_DOOMED_REVIEWS})",
    ),
    ("coach_review", "DELETE FROM coach_review WHERE data_generation < :g"),
    (
        "meal_item",
        (
            "DELETE FROM meal_item WHERE data_generation < :g "
            "OR meal_id IN (SELECT id FROM meal WHERE data_generation < :g) "
            f"OR requested_product_id IN ({_DOOMED_PRODUCTS}) "
            f"OR resolved_product_id IN ({_DOOMED_PRODUCTS}) "
            f"OR override_ids && ARRAY({_DOOMED_OVERRIDES})::integer[]"
        ),
    ),
    ("meal", "DELETE FROM meal WHERE data_generation < :g"),
    ("day", "DELETE FROM day WHERE data_generation < :g"),
    ("idempotency_key", "DELETE FROM idempotency_key"),
    ("weight_entry", "DELETE FROM weight_entry WHERE data_generation < :g"),
    ("profile", "DELETE FROM profile WHERE data_generation < :g"),
    (
        "saved_meal_item",
        (
            "DELETE FROM saved_meal_item WHERE "
            "saved_meal_id IN (SELECT id FROM saved_meal WHERE data_generation < :g) "
            f"OR product_id IN ({_DOOMED_PRODUCTS})"
        ),
    ),
    ("saved_meal", "DELETE FROM saved_meal WHERE data_generation < :g"),
    (
        "alias",
        f"DELETE FROM alias WHERE data_generation < :g OR product_id IN ({_DOOMED_PRODUCTS})",
    ),
    ("nutrient_override", f"DELETE FROM nutrient_override WHERE id IN ({_DOOMED_OVERRIDES})"),
    # Cascades to source_record, profile_revision, nutrient_value, portion, product_state.
    ("product", "DELETE FROM product WHERE owner = 'user' AND data_generation < :g"),
]


def purge_below(conn: Connection, generation: int) -> dict[str, int]:
    """Delete every user row whose generation is below ``generation``. Returns counts."""
    counts: dict[str, int] = {}
    for table, sql in _PURGE:
        counts[table] = conn.execute(sa.text(sql), {"g": generation}).rowcount
    return counts


_GENERATION_TABLES = (
    "meal_item",
    "meal",
    "day",
    "weight_entry",
    "profile",
    "saved_meal",
    "alias",
    "nutrient_override",
    "coach_review",
    "llm_call",
    "coach_feedback",
)
# Rows that would be left dangling if a cascade or purge step were missed.
_ORPHAN_CHECKS: dict[str, str] = {
    "user_product_below": "SELECT count(*) FROM product WHERE owner = 'user' "
    "AND data_generation < :g",
    "meal_item_orphan_meal": "SELECT count(*) FROM meal_item i "
    "WHERE NOT EXISTS (SELECT 1 FROM meal m WHERE m.id = i.meal_id)",
    "meal_item_orphan_product": "SELECT count(*) FROM meal_item i WHERE NOT EXISTS "
    "(SELECT 1 FROM product p WHERE p.id = i.resolved_product_id) OR NOT EXISTS "
    "(SELECT 1 FROM product p WHERE p.id = i.requested_product_id)",
    "meal_item_orphan_override": "SELECT count(*) FROM meal_item i WHERE EXISTS "
    "(SELECT 1 FROM unnest(i.override_ids) o(id) "
    "WHERE NOT EXISTS (SELECT 1 FROM nutrient_override n WHERE n.id = o.id))",
    "saved_meal_item_orphan": "SELECT count(*) FROM saved_meal_item s WHERE NOT EXISTS "
    "(SELECT 1 FROM saved_meal m WHERE m.id = s.saved_meal_id) OR NOT EXISTS "
    "(SELECT 1 FROM product p WHERE p.id = s.product_id)",
    "alias_orphan": "SELECT count(*) FROM alias a WHERE NOT EXISTS "
    "(SELECT 1 FROM product p WHERE p.id = a.product_id)",
    **{
        f"{t}_orphan": f"SELECT count(*) FROM {t} c WHERE NOT EXISTS "
        "(SELECT 1 FROM product p WHERE p.id = c.product_id)"
        for t in ("source_record", "profile_revision", "portion", "product_state")
    },
    "llm_call_orphan_review": "SELECT count(*) FROM llm_call l WHERE l.review_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM coach_review r WHERE r.id = l.review_id)",
    "coach_feedback_orphan_review": "SELECT count(*) FROM coach_feedback f WHERE NOT EXISTS "
    "(SELECT 1 FROM coach_review r WHERE r.id = f.review_id)",
    "nutrient_value_orphan": "SELECT count(*) FROM nutrient_value v WHERE NOT EXISTS "
    "(SELECT 1 FROM profile_revision r WHERE r.id = v.revision_id)",
}


def verify_below(conn: Connection, generation: int) -> dict[str, int]:
    """Count rows that should not exist after ``purge_below(generation)``. All must be 0."""
    out = {
        f"{t}_below": conn.execute(
            sa.text(f"SELECT count(*) FROM {t} WHERE data_generation < :g"), {"g": generation}
        ).scalar_one()
        for t in _GENERATION_TABLES
    }
    for name, sql in _ORPHAN_CHECKS.items():
        out[name] = conn.execute(sa.text(sql), {"g": generation}).scalar_one()
    return out


def append_ledger(path: Path, generation: int, at: datetime | None = None) -> None:
    line = json.dumps({"generation": generation, "at": (at or datetime.now(UTC)).isoformat()})
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def ledger_max_generation(path: Path) -> int | None:
    """Max generation in the ledger. Every line is authoritative, even one whose
    transaction never committed. Raises if the file is missing or unreadable."""
    gens = [
        int(json.loads(line)["generation"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return max(gens) if gens else None


def delete_all_data(engine: Engine, oid: str, ledger_path: Path) -> dict[str, Any]:
    check_ledger_volume(ledger_path)
    with engine.begin() as conn:
        _ensure_account(conn, oid)
        new = conn.execute(
            sa.update(user_account)
            .where(user_account.c.entra_oid == oid)
            .values(data_generation=user_account.c.data_generation + 1)
            .returning(user_account.c.data_generation)
        ).scalar_one()
        # Write-ahead: the ledger line is durable before the deletion commits. If the commit
        # then fails, a later restore still purges what the user asked to delete.
        append_ledger(ledger_path, new)
        counts = purge_below(conn, new)
    return {"generation": new, "deleted": counts}


# --- export ------------------------------------------------------------------------------

_USER_PRODUCTS = "SELECT id FROM product WHERE owner = 'user'"
_EXPORT: list[tuple[str, str]] = [
    ("products", "SELECT * FROM product WHERE owner = 'user' ORDER BY id"),
    (
        "source_records",
        f"SELECT * FROM source_record WHERE product_id IN ({_USER_PRODUCTS}) ORDER BY id",
    ),
    (
        "profile_revisions",
        f"SELECT * FROM profile_revision WHERE product_id IN ({_USER_PRODUCTS}) ORDER BY id",
    ),
    (
        "nutrient_values",
        (
            "SELECT * FROM nutrient_value WHERE revision_id IN (SELECT id FROM profile_revision "
            f"WHERE product_id IN ({_USER_PRODUCTS})) ORDER BY revision_id, nutrient"
        ),
    ),
    ("portions", f"SELECT * FROM portion WHERE product_id IN ({_USER_PRODUCTS}) ORDER BY id"),
    (
        "product_states",
        (
            "SELECT * FROM product_state WHERE release_id IS NULL "
            f"AND product_id IN ({_USER_PRODUCTS}) ORDER BY id"
        ),
    ),
    ("nutrient_overrides", "SELECT * FROM nutrient_override ORDER BY id"),
    ("meals", "SELECT * FROM meal ORDER BY id"),
    ("meal_items", "SELECT * FROM meal_item ORDER BY id"),
    ("days", "SELECT * FROM day ORDER BY day"),
    ("weights", "SELECT * FROM weight_entry ORDER BY day"),
    ("profile", "SELECT * FROM profile"),
    ("saved_meals", "SELECT * FROM saved_meal ORDER BY id"),
    ("saved_meal_items", "SELECT * FROM saved_meal_item ORDER BY id"),
    ("aliases", "SELECT * FROM alias ORDER BY id"),
    ("coach_reviews", "SELECT * FROM coach_review ORDER BY id"),
    ("llm_calls", "SELECT * FROM llm_call ORDER BY id"),
    ("coach_feedback", "SELECT * FROM coach_feedback ORDER BY id"),
]


def jsonable(v: Any) -> Any:
    # Decimals become strings; NULL (unknown) stays null and is never turned into 0.
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v


def build_export(conn: Connection) -> dict[str, Any]:
    return {
        name: [
            {k: jsonable(v) for k, v in row.items()}
            for row in conn.execute(sa.text(sql)).mappings()
        ]
        for name, sql in _EXPORT
    }


def export_data(engine: Engine, oid: str) -> dict[str, Any]:
    """Export every user table. Raises ``StaleGeneration`` if a deletion raced it (A31)."""
    with engine.connect() as conn:
        started = current_generation(conn, oid)
    snapshot = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    with snapshot as conn, conn.begin():
        data = build_export(conn)
    with engine.connect() as conn:
        if current_generation(conn, oid) != started:
            raise StaleGeneration("data was deleted while the export was being built")
    return {"generation": started, "exported_at": datetime.now(UTC).isoformat(), "data": data}
