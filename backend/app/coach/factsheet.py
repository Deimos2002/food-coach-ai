"""Weekly fact sheet (weekly-coach spec §2.6): the one anonymous document the coach may see.

It joins the week's logged items (their frozen ``meal_item.nutrients`` snapshots), day
statuses, daily targets, guideline cards and the live catalogue, and runs the pure domain
rules over them: ``evaluate_week`` for facts, ``top_contributors`` for who drove a fact and
``find_swaps`` for catalogue variants. No identity goes in: the only names are food and
product names. Decimals are carried as strings, unknown values are listed, never zeroed.

``item_refs`` on each contributor link the Week screen's numbers back to their items, and
``product_id`` on each swap links it to the catalogue; both are excluded from the hash and
stripped by ``llm_view``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.tables import product
from app.domain.contributors import ItemContribution, top_contributors
from app.domain.day_state import DayItem
from app.domain.day_status import DayStatus
from app.domain.item_calc import ItemNutrient
from app.domain.quantity import Unit
from app.domain.swaps import SwapCandidate, find_swaps, normalize_name
from app.domain.values import Nutrient, Qualifier, Range
from app.domain.week_rules import (
    Card,
    RuleFact,
    WeekDay,
    evaluate_week,
    iso_week_days,
    week_coverage,
)
from app.services import weights as weights_svc
from app.services.logging import (
    COOKING_ADDITION_NUTRIENTS,
    day_table,
    dec,
    meal,
    meal_item,
    nutrients_from_json,
)
from app.services.profile import ProfileError, get_targets, profile_table

SCHEMA_VERSION = 1
# Facts that call for contributors; swaps only where less of N is the direction of travel.
_CONTRIBUTOR_CLAIMS = frozenset({"exceeds", "above", "below"})
_SWAP_CLAIMS = frozenset({"exceeds", "above"})
_UNKNOWN = ItemNutrient(False, None, Qualifier.NONE)
_TENTH = Decimal("0.1")

_LIVE_PRODUCTS = """
    WITH live AS (
        SELECT DISTINCT ON (ps.product_id) ps.product_id, ps.active_revision_id
        FROM product_state ps
        WHERE (ps.release_id IS NULL
           OR ps.release_id = (SELECT release_id FROM active_release WHERE singleton))
          AND ps.status = 'active'
        ORDER BY ps.product_id, ps.release_id ASC NULLS FIRST
    )
    SELECT p.id, p.name, p.brand, p.basis_unit,
           nv.known, nv.lower, nv.upper, nv.qualifier, nv.source, nv.derivation,
           nv.verification
    FROM product p
    JOIN live ON live.product_id = p.id
    JOIN nutrient_value nv ON nv.revision_id = live.active_revision_id AND nv.nutrient = :n
"""
_TARGET_SQL = sa.text(_LIVE_PRODUCTS + " WHERE p.id = :pid")
# Pre-filter by basis unit, brand presence and name: every token of the target's
# variant-stripped normalised name must occur in the candidate's ``search_text`` (the
# normalised name and brand). This only narrows; find_swaps still does the exact
# accent-insensitive brand and variant-name comparison.
_CANDIDATES_SQL = sa.text(
    _LIVE_PRODUCTS
    + " WHERE p.basis_unit = :bu AND p.id <> :pid"
    " AND ((CAST(:has_brand AS boolean) AND p.brand IS NOT NULL)"
    " OR (NOT CAST(:has_brand AS boolean) AND p.brand IS NULL))"
    " AND NOT EXISTS (SELECT 1 FROM unnest(CAST(:toks AS text[])) AS t(tok)"
    " WHERE strpos(p.search_text, t.tok) = 0)"
    " ORDER BY p.id"
)


def _candidate(row: Mapping[str, Any]) -> SwapCandidate:
    return SwapCandidate(
        product_id=row["id"],
        name=row["name"],
        brand=row["brand"],
        basis_unit=Unit(row["basis_unit"]),
        value=catalogue.row_to_value(row),
    )


_SwapResult = tuple[SwapCandidate | None, list[SwapCandidate]]


def _swaps(
    conn: Connection, product_id: int, nutrient: Nutrient,
    cache: dict[tuple[int, Nutrient], _SwapResult],
) -> _SwapResult:
    key = (product_id, nutrient)
    if key not in cache:
        cache[key] = _lookup_swaps(conn, product_id, nutrient)
    return cache[key]


def _lookup_swaps(conn: Connection, product_id: int, nutrient: Nutrient) -> _SwapResult:
    row = conn.execute(_TARGET_SQL, {"n": nutrient.value, "pid": product_id}).mappings().first()
    if row is None:  # not live any more, or no value for N: never guess
        return None, []
    target = _candidate(row)
    rows = conn.execute(
        _CANDIDATES_SQL,
        {"n": nutrient.value, "pid": product_id, "bu": row["basis_unit"],
         "has_brand": row["brand"] is not None,
         "toks": normalize_name(row["name"]).split()},
    ).mappings()
    return target, find_swaps(target, [_candidate(r) for r in rows])


def _load_items(conn: Connection, days: list[date]) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(
            meal.c.day,
            meal_item.c.meal_id,
            meal_item.c.id,
            meal_item.c.resolved_product_id,
            meal_item.c.nutrients,
            product.c.name,
            product.c.kind,
            product.c.owner,
            product.c.includes_cooking_additions,
        )
        .join(meal, meal.c.id == meal_item.c.meal_id)
        .join(product, product.c.id == meal_item.c.resolved_product_id)
        .where(meal.c.day.between(days[0], days[-1]))
        .order_by(meal.c.day, meal_item.c.meal_id, meal_item.c.id)
    ).mappings()
    out = []
    for r in rows:
        # A.3, as in the day view: an undeclared user dish is unresolved for salt and fat.
        undeclared = (
            r["kind"] == "dish" and r["owner"] == "user"
            and r["includes_cooking_additions"] != "yes"
        )
        out.append({
            "day": r["day"],
            "meal_id": r["meal_id"],
            "item_id": r["id"],
            "product_id": r["resolved_product_id"],
            "name": r["name"],
            "nutrients": nutrients_from_json(r["nutrients"]),
            "unresolved_for": COOKING_ADDITION_NUTRIENTS if undeclared else frozenset(),
        })
    return out


def _target(conn: Connection, day: date) -> tuple[int | None, int | None]:
    try:
        t = get_targets(conn, day)
    except ProfileError:
        return None, None
    return t["kcal"], t["protein_g"]


def _cards(conn: Connection) -> list[Card]:
    rows = conn.execute(
        sa.text("SELECT id, threshold, verified_by_user_at FROM guideline_card ORDER BY id")
    ).mappings()
    return [Card(r["id"], r["threshold"], r["verified_by_user_at"] is not None) for r in rows]


def _goal(conn: Connection) -> dict[str, str] | None:
    row = conn.execute(
        sa.select(profile_table.c.goal, profile_table.c.pace_pct_per_week)
    ).first()
    if row is None:
        return None
    return {"type": row.goal, "pace_pct_per_week": dec(row.pace_pct_per_week)}


def _trend(conn: Connection, days: list[date]) -> dict[str, str] | None:
    points = [p for p in weights_svc.trend(conn) if days[0] <= p.day <= days[-1]]
    if not points:
        return None
    return {
        "kg_start": dec(points[0].trend.quantize(_TENTH)),
        "kg_end": dec(points[-1].trend.quantize(_TENTH)),
    }


def _share(d: Decimal) -> str:
    return format(d.quantize(Decimal("0.01")), "f")


def _nutrient_unit(nutrient: Nutrient) -> str:
    return "kcal" if nutrient is Nutrient.ENERGY_KCAL else "g"


def _fact_json(
    conn: Connection,
    fact: RuleFact,
    items: list[dict[str, Any]],
    counters: dict[str, int],
    swap_cache: dict[tuple[int, Nutrient], _SwapResult],
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": fact.id,
        "rule": fact.rule,
        "claim": fact.claim,
        "values": dict(fact.values),
        "days": [d.isoformat() for d in fact.days],
        "card_id": fact.card_id,
        "message": fact.message,
        "contributors": [],
        "unknown_for": [],
        "additions_undeclared": [],
        "swaps": [],
    }
    if fact.claim not in _CONTRIBUTOR_CLAIMS or not fact.days:
        return out
    on_days = [it for it in items if it["day"] in set(fact.days)]
    contributions = []
    undeclared_ids: set[int] = set()
    for it in on_days:
        value = it["nutrients"].get(fact.nutrient, _UNKNOWN)
        if fact.nutrient in it["unresolved_for"] and value.known and value.range is not None:
            # A.3, as in the day view: a dish with undeclared cooking additions keeps its
            # recorded lower bound but is open-ended, so its upper bound (and therefore the
            # fact's total upper bound) is unknown. It is listed under
            # ``additions_undeclared`` (the day view's name for it), not ``unknown_for``,
            # which holds values that are entirely unknown.
            value = ItemNutrient(True, Range(value.range.lower, None), value.qualifier)
            undeclared_ids.add(it["product_id"])
        contributions.append(ItemContribution(it["product_id"], it["name"], value))
    top, unknown_for = top_contributors(contributions)
    out["unknown_for"] = sorted(set(unknown_for))
    out["additions_undeclared"] = sorted(
        {it["name"] for it in on_days if it["product_id"] in undeclared_ids}
    )
    for c in top:
        counters["C"] += 1
        cid = f"C{counters['C']}"
        out["contributors"].append({
            "id": cid,
            "name": c.name,
            # Shares keep their two places ("0.38"), as quantized by top_contributors.
            "share": [_share(c.share[0]), None if c.share[1] is None else _share(c.share[1])],
            "additions_undeclared": c.product_id in undeclared_ids,
            "item_refs": [
                {"day": it["day"].isoformat(), "meal_id": it["meal_id"], "item_id": it["item_id"]}
                for it in on_days
                if it["product_id"] == c.product_id
            ],
        })
        if fact.claim not in _SWAP_CLAIMS:
            continue
        target, found = _swaps(conn, c.product_id, fact.nutrient, swap_cache)
        for s in found:
            counters["S"] += 1
            out["swaps"].append({
                "id": f"S{counters['S']}",
                "for": cid,
                "name": s.name,
                # For the Week screen; stripped by llm_view (the LLM only needs S#).
                "product_id": s.product_id,
                "values": {
                    "from_lower": dec(target.value.range.lower),
                    "to_upper": dec(s.value.range.upper),
                    "unit": f"{_nutrient_unit(fact.nutrient)}/100{s.basis_unit.value}",
                    # Both values are the live catalogue revision's per-100 values, not
                    # the logged snapshot (user overrides are not applied).
                    "basis": "catalogue_per_100",
                },
            })
    return out


# Keys the LLM never sees and the hash ignores: item links and swap product ids.
_PRIVATE_KEYS = frozenset({"item_refs", "product_id"})


def _strip_refs(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_refs(v) for k, v in obj.items() if k not in _PRIVATE_KEYS}
    if isinstance(obj, list):
        return [_strip_refs(v) for v in obj]
    return obj


def sheet_hash(sheet: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical sorted JSON, without ``hash``, ``item_refs`` and swap
    ``product_id``."""
    body = _strip_refs({k: v for k, v in sheet.items() if k != "hash"})
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assemble_sheet(
    *, week: str, coverage: Any, days_included: list[str], days_missing: list[str],
    facts: list[dict[str, Any]], goal: dict[str, str] | None, trend: dict[str, str] | None,
) -> dict[str, Any]:
    """The sheet's top level, with its hash. Pure: shared by ``build_fact_sheet``, the eval
    fixture generator and the fixture key-set test."""
    sheet: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "week": week,
        "coverage": coverage,
        "days_included": days_included,
        "days_missing": days_missing,
        "facts": facts,
        "goal": goal,
        "trend": trend,
    }
    sheet["hash"] = sheet_hash(sheet)
    return sheet


def build_fact_sheet(conn: Connection, iso_week: str) -> dict[str, Any]:
    """The week's fact sheet. Raises ValueError for a malformed or impossible ISO week.

    Call it inside ``snapshot_connection`` so every read sees one snapshot.
    """
    days = iso_week_days(iso_week)
    statuses = {
        r.day: DayStatus(r.status)
        for r in conn.execute(
            sa.select(day_table.c.day, day_table.c.status).where(
                day_table.c.day.between(days[0], days[-1])
            )
        )
    }
    items = _load_items(conn, days)
    week_days: list[WeekDay] = []
    for d in days:
        status = statuses.get(d, DayStatus.NOT_LOGGED)
        kcal, protein = _target(conn, d)
        week_days.append(WeekDay(
            day=d,
            status=status,
            items=tuple(
                DayItem(it["name"], it["nutrients"], it["unresolved_for"])
                for it in items if it["day"] == d
            ),
            target_kcal=kcal,
            target_protein_g=protein,
        ))
    logged = [w for w in week_days if w.status is not DayStatus.NOT_LOGGED]
    facts = evaluate_week(iso_week, logged, _cards(conn))
    counters = {"C": 0, "S": 0}
    swap_cache: dict[tuple[int, Nutrient], _SwapResult] = {}
    return assemble_sheet(
        week=iso_week,
        coverage=week_coverage(iso_week, week_days),
        days_included=[w.day.isoformat() for w in logged],
        days_missing=[w.day.isoformat() for w in week_days if w not in logged],
        facts=[_fact_json(conn, f, items, counters, swap_cache) for f in facts],
        goal=_goal(conn),
        trend=_trend(conn, days),
    )


def complete_days(conn: Connection, iso_week: str) -> int:
    days = iso_week_days(iso_week)
    return conn.execute(
        sa.select(sa.func.count()).select_from(day_table).where(
            day_table.c.day.between(days[0], days[-1]),
            day_table.c.status == DayStatus.COMPLETE.value,
        )
    ).scalar_one()


def llm_view(sheet: Mapping[str, Any]) -> dict[str, Any]:
    """What the LLM may see: the sheet without ``item_refs`` and swap ``product_id``.
    Does not mutate ``sheet``."""
    return _strip_refs(copy.deepcopy(dict(sheet)))
