"""Accent/typo-tolerant product search (Task 13).

``search_text`` on ``product`` holds the normalized name+brand+aliases, plus the same
text with spaces removed (so "redbull" without a space still matches "red bull"). The
GIN trigram index (migration ``0003_search``) makes ``%`` (similarity) and ``LIKE``
lookups fast.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.domain.values import Nutrient


def normalize(text: str) -> str:
    """Lowercase, strip accents (NFKD, drop combining marks), collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # NFKD leaves œ/æ intact, so fold them explicitly ("oeuf" must match "Œuf").
    return " ".join(stripped.lower().replace("œ", "oe").replace("æ", "ae").split())


def search_text_for(name: str, brand: str | None, aliases: Sequence[str] = ()) -> str:
    """The normalized ``name brand aliases...`` text, plus the same text with no spaces.

    The space-free copy lets a query typed with no spaces ("redbull peche") still match
    a name stored with spaces ("Red Bull Pêche"), since ``LIKE``/trigram similarity work
    on substrings, not tokens.
    """
    parts = [name, brand or "", *aliases]
    base = normalize(" ".join(p for p in parts if p))
    return f"{base} {base.replace(' ', '')}"


def _escape_like(text: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so ``text`` is matched as a literal substring."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(conn: Connection, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Active products (catalogue + user-owned) matching ``q``, best match first.

    Review Focus 5: a query that normalizes to fewer than 2 characters never touches the
    database and returns ``[]`` immediately.

    Only products whose live ``product_state`` row has ``status = 'active'`` (Status.ACTIVE,
    spec A.6) are returned; a RETIRED or MERGED live state is excluded.
    """
    nq = normalize(q)
    if len(nq) < 2:
        return []
    like_nq = _escape_like(nq)

    sql = sa.text(
        """
        WITH live AS (
            SELECT DISTINCT ON (ps.product_id) ps.product_id, ps.active_revision_id
            FROM product_state ps
            WHERE (ps.release_id IS NULL
               OR ps.release_id = (SELECT release_id FROM active_release WHERE singleton))
              AND ps.status = 'active'
            ORDER BY ps.product_id, ps.release_id ASC NULLS FIRST
        ),
        hist AS (
            SELECT resolved_product_id AS product_id, count(*) AS n
            FROM meal_item
            GROUP BY resolved_product_id
        )
        SELECT
            p.id AS product_id,
            p.name AS name,
            p.name_en AS name_en,
            p.brand AS brand,
            EXISTS (
                SELECT 1 FROM profile_revision pr
                JOIN source_record sr ON sr.id = pr.source_record_id
                WHERE pr.id = live.active_revision_id AND sr.source = 'label'
                    AND NULLIF(sr.raw->>'chain', '') IS NOT NULL
            ) AS is_fast_food,
            p.basis_unit AS basis_unit,
            nv.lower AS kcal_per_100,
            COALESCE(hist.n, 0) AS history_count,
            similarity(p.search_text, :nq) AS sim
        FROM product p
        JOIN live ON live.product_id = p.id
        LEFT JOIN nutrient_value nv
            ON nv.revision_id = live.active_revision_id AND nv.nutrient = 'energy_kcal' AND nv.known
        LEFT JOIN hist ON hist.product_id = p.id
        WHERE p.search_text % :nq OR p.search_text LIKE '%' || :like_nq || '%' ESCAPE '\\'
        ORDER BY sim DESC, history_count DESC
        LIMIT :limit
        """
    )
    rows = conn.execute(sql, {"nq": nq, "like_nq": like_nq, "limit": limit}).mappings().all()
    results: list[dict[str, Any]] = []
    for row in rows:
        kcal = row["kcal_per_100"]
        results.append(
            {
                "type": "product",
                "product_id": row["product_id"],
                "name": row["name"],
                "name_en": row["name_en"],
                "brand": row["brand"],
                "is_fast_food": row["is_fast_food"],
                "basis_unit": row["basis_unit"],
                "portions": _portions(conn, row["product_id"]),
                "kcal_per_100": None if kcal is None else kcal if isinstance(kcal, Decimal) else Decimal(str(kcal)),
            }
        )
    return results + _meals(conn, nq)


def _meals(conn: Connection, nq: str, limit: int = 5) -> list[dict[str, Any]]:
    # shortcut: filters in Python (accent folding), fine for a curated set of hundreds of templates.
    rows = conn.execute(
        sa.text("SELECT id, name, name_en, cuisine, chain, estimate FROM meal_template ORDER BY name")
    ).mappings()
    meals = [
        {"type": "meal", "template_id": r["id"], **{k: r[k] for k in ("name", "name_en", "cuisine", "chain", "estimate")}}
        for r in rows
        if nq in normalize(r["name"]) or (r["name_en"] and nq in normalize(r["name_en"]))
    ]
    return meals[:limit]


def _portions(conn: Connection, product_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.text(
            "SELECT label, amount, kind FROM portion WHERE product_id = :p ORDER BY id"
        ),
        {"p": product_id},
    ).mappings()
    return [{"label": r["label"], "amount": r["amount"], "kind": r["kind"]} for r in rows]


__all__ = ["Nutrient", "normalize", "search", "search_text_for"]
