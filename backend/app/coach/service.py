"""Weekly coach service (weekly-coach spec §3.1, coach Task 7).

``generate_review`` runs in three short phases so that no lock or transaction is held
across the network call:

A. *Claim* (one transaction): ``require_generation`` + ``pg_advisory_xact_lock`` on the
   week, then look for an existing review for this sheet hash. If one exists it is
   returned; if one is in flight (a ``pending`` placeholder row) the caller gets
   ``in_progress``. Otherwise the deterministic template is computed, the region gate and
   budget are checked, and either a finished template review is stored, or the budget is
   reserved and a ``pending`` placeholder row is inserted. Committing this transaction is
   what makes concurrent requests (and a regenerate racing the first generation) see the
   placeholder, so exactly one call is dispatched (A20, Review Focus 4).
B. *Call* (no transaction): up to two LLM attempts, each validated; the second carries
   the validator errors.
C. *Finish*: the budget is settled (actual cost) or released in its own transaction, then
   the placeholder is turned into the final review and the ``llm_call`` rows are written,
   under ``require_generation(expected=...)`` so a concurrent ``DELETE /me/data`` wins.

The placeholder is a normal ``coach_review`` row with ``prompt_version = 'pending'``
(source ``template``, the template suggestions), because migration 0005 has no status
column. A placeholder older than ``PENDING_TIMEOUT`` (a crashed worker) is finalised as
the template with ``fallback_reason='llm_error'``.

Catalogue names for the validator's name check (``catalogue_names_for``): every live
product whose normalised name has a token of 3+ characters that occurs in the output text
(spaces removed), or whose whole name occurs in it. This is computed per output, so it is
bounded by what the model actually wrote, and it is a superset of every name the validator
could flag except a pair probe made only of two 1-2 character tokens.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from app.coach import budget
from app.coach.factsheet import build_fact_sheet, complete_days, llm_view
from app.coach.knowledge import attach_knowledge, query_embedder, with_sources
from app.coach.llm import LlmClient, LlmError, LlmResult, cost_eur
from app.config import BACKEND_DIR, Settings
from app.domain.coach_validator import (
    KINDS,
    canon,
    spaceless,
    uses_passage,
    validate_output,
)
from app.services.logging import snapshot_connection
from app.services.privacy import StaleGeneration, jsonable, require_generation
from app.services.search import normalize

log = logging.getLogger(__name__)

PROMPT_VERSION = "v7"
PROMPT_PATH = BACKEND_DIR / "prompts" / "coach" / f"{PROMPT_VERSION}.md"
PENDING = "pending"
PENDING_TIMEOUT = timedelta(minutes=2)
MIN_COMPLETE_DAYS = 3
MAX_TOKENS = 800
TEMPERATURE = 0.3
# Worst-case input growth on the retry: the first output plus the validator errors.
_RETRY_EXTRA_TOKENS = MAX_TOKENS + 500
# Upper bound on the guideline passages added to the prompt (3 facts x 2 passages).
_KNOWLEDGE_MAX_CHARS = 8000
REGION_NOTE = "AI suggestions are off: EU-only processing isn't available yet."
# Shown instead of suggestions when the model produced no valid review. There is no static
# fallback text: a week either gets AI suggestions that passed the checker, or none.
UNAVAILABLE = {
    "none": "AI suggestions are switched off.",
    "budget": "This month's AI budget is used up, so no suggestions were generated.",
    "validation": "The AI's suggestions did not pass the accuracy checks, so none are shown.",
    "llm_error": "The AI could not produce suggestions for this week.",
}
NOTHING_MESSAGE = (
    "Nothing to suggest this week: none of the checks found something to change or to "
    "keep going with, based on your completed log."
)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

OUTPUT_SCHEMA: dict[str, Any] = {
    "name": "coach_review",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["suggestions"],
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "title", "body", "fact_ids", "swap_ids",
                                 "source_ids"],
                    "properties": {
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "fact_ids": {"type": "array", "items": {"type": "string"}},
                        "swap_ids": {"type": "array", "items": {"type": "string"}},
                        "source_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    },
}


class RegenerateLimit(Exception):
    """The week's one regenerate has been used (409)."""


class ReviewNotFound(Exception):
    pass


class FeedbackInvalid(Exception):
    pass


def _coachable(sheet: dict) -> bool:
    """False when no rule could be judged (no facts, or every claim is ``cannot_tell``):
    there is nothing to coach, so no model call is made."""
    return any(f.get("claim") != "cannot_tell" for f in (sheet or {}).get("facts", []))


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def region_allowed(settings: Settings) -> bool:
    return settings.azure_openai_deployment_type == "datazone" or settings.coach_allow_global


def prices_set(settings: Settings) -> bool:
    """Both token prices must be positive: with a zero price the budget cap cannot bound
    spending, so the LLM is refused (fail-closed)."""
    return settings.coach_price_in_per_1k > 0 and settings.coach_price_out_per_1k > 0


def strip_fence(text: str) -> str:
    m = _FENCE.match(text)
    return m.group(1) if m else text


def prioritise(suggestions: list[dict], sheet: dict) -> list[dict]:
    """Most important first, decided here and not by the model: it words the advice, it does
    not choose the order. Energy working against the goal, then the limit exceeded on the
    most days, then the other problems; ``keep_going`` comes last and is dropped once two
    problems are already shown."""
    facts = {f["id"]: f for f in sheet.get("facts", [])}
    goal = (sheet.get("goal") or {}).get("type")

    def rank(fact: dict) -> tuple[int, int]:
        rule, claim = fact.get("rule"), fact.get("claim")
        if rule == "energy_vs_target" and (claim, goal) in {("above", "lose"), ("below", "gain")}:
            return (0, 0)
        if claim == "exceeds":
            return (1, -int(fact["values"].get("days_exceeding", "0")))
        return (2, 0)

    def key(s: dict) -> tuple[int, int]:
        if s["kind"] == "keep_going":
            return (9, 0)
        return min((rank(facts[i]) for i in s["fact_ids"] if i in facts), default=(2, 0))

    # A listed passage the body does not draw on is not a source: drop it, don't reject.
    passages = {k["id"]: k for k in sheet.get("knowledge") or []}
    suggestions = [
        {**s, "source_ids": [i for i in s["source_ids"]
                             if i in passages and uses_passage(s["body"], passages[i])]}
        if s.get("source_ids") else s
        for s in suggestions
    ]
    ordered = sorted(suggestions, key=key)  # stable: ties keep the model's order
    if sum(s["kind"] != "keep_going" for s in ordered) >= 2:
        ordered = [s for s in ordered if s["kind"] != "keep_going"]
    return ordered


# --- catalogue names -----------------------------------------------------------------------

_LIVE_NAMES = sa.text(
    """
    SELECT DISTINCT p.name FROM product p
    JOIN product_state ps ON ps.product_id = p.id
    WHERE ps.status = 'active'
      AND (ps.release_id IS NULL
           OR ps.release_id = (SELECT release_id FROM active_release WHERE singleton))
    """
)


def catalogue_names_for(conn: Connection, output_text: str) -> list[str]:
    """Live product names the validator must check ``output_text`` against (see module doc)."""
    hay = spaceless(normalize(canon(output_text)))
    out = []
    for (name,) in conn.execute(_LIVE_NAMES):
        n = normalize(canon(name))
        if not n:
            continue
        if spaceless(n) in hay or any(len(t) >= 3 and t in hay for t in n.split()):
            out.append(name)
    return out


# --- rows ------------------------------------------------------------------------------------

_REVIEW_COLS = (
    "id, iso_week, fact_sheet_hash, generation_no, fact_sheet, suggestions, source, "
    "fallback_reason, prompt_version, created_at, data_generation"
)


def _review_json(row: Any, settings: Settings) -> dict[str, Any]:
    review = {
        k: jsonable(row[k])
        for k in (
            "id", "iso_week", "generation_no", "fact_sheet", "suggestions", "source",
            "fallback_reason", "prompt_version", "created_at",
        )
    }
    out: dict[str, Any] = {"status": "ok", "review": review}
    if row["suggestions"]:
        return out
    if not _coachable(row["fact_sheet"]):
        out["status"] = "nothing_to_suggest"
        out["message"] = NOTHING_MESSAGE
    else:
        reason = row["fallback_reason"] or "llm_error"
        out["status"] = "unavailable"
        out["message"] = (REGION_NOTE if reason == "none" and not region_allowed(settings)
                          else UNAVAILABLE[reason])
    return out


def _insert_review(
    conn: Connection, *, sheet: dict, gen_no: int, suggestions: list, source: str,
    fallback: str | None, prompt_version: str, generation: int,
) -> Any:
    return conn.execute(
        sa.text(
            "INSERT INTO coach_review (iso_week, fact_sheet_hash, generation_no, fact_sheet, "
            "suggestions, source, fallback_reason, prompt_version, data_generation) "
            "VALUES (:w, :h, :g, CAST(:fs AS jsonb), CAST(:s AS jsonb), :src, :fb, :pv, :dg) "
            f"RETURNING {_REVIEW_COLS}"
        ),
        {
            "w": sheet["week"], "h": sheet["hash"], "g": gen_no,
            "fs": json.dumps(sheet, ensure_ascii=False),
            "s": json.dumps(suggestions, ensure_ascii=False),
            "src": source, "fb": fallback, "pv": prompt_version, "dg": generation,
        },
    ).mappings().one()


def _finish_review(
    conn: Connection, review_id: int, suggestions: list, source: str, fallback: str | None
) -> Any:
    return conn.execute(
        sa.text(
            "UPDATE coach_review SET suggestions = CAST(:s AS jsonb), source = :src, "
            "fallback_reason = :fb, prompt_version = :pv WHERE id = :id "
            f"RETURNING {_REVIEW_COLS}"
        ),
        {
            "id": review_id, "s": json.dumps(suggestions, ensure_ascii=False),
            "src": source, "fb": fallback, "pv": PROMPT_VERSION,
        },
    ).mappings().one_or_none()


# --- generate ---------------------------------------------------------------------------------


def _max_cost(settings: Settings, prompt_chars: int) -> Decimal:
    # Conservative: assume 1 token per 2 characters (real ratio is ~4 for JSON/English).
    in1 = math.ceil(prompt_chars / 2)

    def cost(tokens_in: int) -> Decimal:
        return cost_eur(LlmResult("", tokens_in, MAX_TOKENS, 0, ""), settings)

    return cost(in1) + cost(in1 + _RETRY_EXTRA_TOKENS)


def _reservation(settings: Settings, sheet: dict) -> Decimal:
    """The amount reserved for ``sheet``; deterministic so a stale placeholder's reservation
    can be recomputed from its stored fact sheet (same prompt file and prices)."""
    chars = (len(load_prompt()) + len(json.dumps(llm_view(sheet), ensure_ascii=False))
             + _KNOWLEDGE_MAX_CHARS)
    return _max_cost(settings, chars)


def _attempt_row(
    settings: Settings, attempt: int, result: LlmResult | None, error: str | None,
    ok: bool | None, errors: list[dict] | None, prompt_text: str, response: str | None,
) -> dict[str, Any]:
    log = settings.coach_log_text
    return {
        "model": result.model if result else settings.azure_openai_deployment,
        "deployment": settings.azure_openai_deployment,
        "input_tokens": result.input_tokens if result else None,
        "output_tokens": result.output_tokens if result else None,
        "cost_eur": cost_eur(result, settings) if result else None,
        "latency_ms": result.latency_ms if result else None,
        "validation_ok": ok,
        "validation_errors": json.dumps(
            errors if error is None else [{"code": "llm_error", "detail": error}]
        ),
        "attempt": attempt,
        "prompt_text": prompt_text if log else None,
        "response_text": response if log else None,
    }


class _Spend:
    """Phase B accumulator, updated after every completed call so the real cost and the
    ``llm_call`` rows survive an exception raised by a later step."""

    def __init__(self) -> None:
        self.eur = Decimal(0)
        self.rows: list[dict[str, Any]] = []


def _call_llm(
    engine: Engine, client: LlmClient, settings: Settings, sheet: dict, acc: _Spend
) -> tuple[list | None, str | None]:
    """Phase B. Returns (suggestions or None, fallback reason); cost and rows go to ``acc``."""
    system = load_prompt()
    view = llm_view(sheet)
    if settings.coach_knowledge_enabled:
        view = attach_knowledge(view, query_embedder(settings))
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": json.dumps(view, ensure_ascii=False)}
    ]
    rows = acc.rows
    for attempt in (1, 2):
        prompt_text = json.dumps({"system": system, "messages": messages}, ensure_ascii=False)
        try:
            result = client.complete(
                system=system, messages=list(messages), json_schema=OUTPUT_SCHEMA,
                max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            )
        except LlmError as exc:
            rows.append(_attempt_row(
                settings, attempt, None, type(exc).__name__, None, None, prompt_text, None
            ))
            return None, "llm_error"
        acc.eur += cost_eur(result, settings)
        text = strip_fence(result.text or "")
        with engine.connect() as conn:
            names = catalogue_names_for(conn, text)
        parsed, errors = validate_output(text, view, names)
        err_json = [{"code": e.code, "detail": e.detail} for e in errors]
        rows.append(_attempt_row(
            settings, attempt, result, None, parsed is not None, err_json or None,
            prompt_text, result.text,
        ))
        if parsed is not None:
            return with_sources(prioritise(parsed["suggestions"], view), view), None
        messages = [
            *messages,
            {"role": "assistant", "content": result.text or ""},
            {
                "role": "user",
                "content": "Your output failed the checker. Fix every error and return only "
                "the corrected JSON.\n" + "\n".join(f"- {e['code']}: {e['detail']}" for e in err_json),
            },
        ]
    return None, "validation"


def _abandon(engine: Engine, oid: str, generation: int, review_id: int) -> None:
    """Exception path: finish the placeholder with no suggestions, if the data still exists."""
    try:
        with engine.begin() as conn:
            require_generation(conn, oid, expected=generation)
            _finish_review(conn, review_id, [], "template", "llm_error")
    except StaleGeneration:
        pass  # deleted meanwhile: nothing to finish


def generate_review(
    engine: Engine, client: LlmClient, settings: Settings, oid: str, iso_week: str,
    regenerate: bool, now: datetime,
) -> dict[str, Any]:
    """POST /coach/weeks/{iso_week}. Raises ValueError (bad week), RegenerateLimit,
    StaleGeneration."""
    with snapshot_connection(engine) as conn:
        sheet = build_fact_sheet(conn, iso_week)
        n_complete = complete_days(conn, iso_week)
    if n_complete < MIN_COMPLETE_DAYS:
        return {"status": "insufficient_data", "complete_days": n_complete}

    today = now.astimezone(UTC).date()
    # --- Phase A: claim ---------------------------------------------------------------
    with engine.begin() as conn:
        generation = require_generation(conn, oid)
        conn.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext('coach:' || :w))"), {"w": iso_week}
        )
        existing = conn.execute(
            sa.text(
                f"SELECT {_REVIEW_COLS} FROM coach_review WHERE iso_week = :w "
                "ORDER BY generation_no DESC, id DESC"
            ),
            {"w": iso_week},
        ).mappings().all()
        pending = [r for r in existing if r["prompt_version"] == PENDING]
        for r in pending:
            if r["created_at"] < now - PENDING_TIMEOUT:  # abandoned by a crashed worker
                # Release its reservation, recomputed exactly as it was reserved.
                budget.release(conn, _reservation(settings, r["fact_sheet"]),
                               r["created_at"].astimezone(UTC).date())
                _finish_review(conn, r["id"], [], "template", "llm_error")
            else:
                return {"status": "in_progress"}
        same = [r for r in existing if r["fact_sheet_hash"] == sheet["hash"]]
        if same and not regenerate:
            row = conn.execute(
                sa.text(f"SELECT {_REVIEW_COLS} FROM coach_review WHERE id = :id"),
                {"id": same[0]["id"]},
            ).mappings().one()
            return _review_json(row, settings)
        gen_no = 1
        if same:  # regenerate of this sheet: allowed once per week
            if any(r["generation_no"] == 2 for r in existing):
                raise RegenerateLimit(f"{iso_week} has already been regenerated")
            gen_no = 2

        def store(suggestions: list, fallback: str) -> dict[str, Any]:
            row = _insert_review(
                conn, sheet=sheet, gen_no=gen_no, suggestions=suggestions, source="template",
                fallback=fallback, prompt_version=PROMPT_VERSION, generation=generation,
            )
            return _review_json(row, settings)

        if not _coachable(sheet):
            return store([], "none")  # nothing coachable: no LLM call
        if not settings.coach_llm_enabled or not region_allowed(settings):
            return store([], "none")
        if not prices_set(settings):
            log.warning("coach: token prices are not set (<= 0); no review generated")
            return store([], "budget")
        reserved = _reservation(settings, sheet)
        if not budget.reserve(conn, reserved, settings.coach_monthly_cap_eur, today):
            return store([], "budget")
        placeholder = _insert_review(
            conn, sheet=sheet, gen_no=gen_no, suggestions=[], source="template",
            fallback="none", prompt_version=PENDING, generation=generation,
        )
    review_id = placeholder["id"]

    # --- Phase B: call (no transaction, no lock) -------------------------------------------
    acc = _Spend()
    try:
        suggestions, fallback = _call_llm(engine, client, settings, sheet, acc)
    except BaseException:
        _abandon(engine, oid, generation, review_id)
        raise
    finally:
        # Whatever happened, the real cost of every completed call is recorded.
        with engine.begin() as conn:
            budget.settle(conn, reserved, acc.eur, today)
    calls = acc.rows

    # --- Phase C: finish -------------------------------------------------------------------
    with engine.begin() as conn:
        require_generation(conn, oid, expected=generation)
        if suggestions is None:
            row = _finish_review(conn, review_id, [], "template", fallback)
        else:
            row = _finish_review(conn, review_id, suggestions, "llm", None)
        if row is None:  # deleted by a concurrent DELETE /me/data
            raise StaleGeneration("review deleted while it was being generated")
        for call in calls:
            conn.execute(
                sa.text(
                    "INSERT INTO llm_call (review_id, model, deployment, prompt_version, "
                    "input_tokens, output_tokens, cost_eur, latency_ms, validation_ok, "
                    "validation_errors, attempt, prompt_text, response_text, data_generation) "
                    "VALUES (:rid, :model, :deployment, :pv, :input_tokens, :output_tokens, "
                    ":cost_eur, :latency_ms, :validation_ok, CAST(:validation_errors AS jsonb), "
                    ":attempt, :prompt_text, :response_text, :dg)"
                ),
                {**call, "rid": review_id, "pv": PROMPT_VERSION, "dg": generation},
            )
    return _review_json(row, settings)


# --- read, feedback, ops ------------------------------------------------------------------


def get_review(engine: Engine, settings: Settings, iso_week: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                f"SELECT {_REVIEW_COLS} FROM coach_review WHERE iso_week = :w "
                "ORDER BY generation_no DESC, id DESC LIMIT 1"
            ),
            {"w": iso_week},
        ).mappings().first()
    if row is None:
        return None
    if row["prompt_version"] == PENDING:
        return {"status": "in_progress"}
    return _review_json(row, settings)


FEEDBACK_VALUES = ("up", "down", "will_try")


def record_feedback(
    engine: Engine, oid: str, review_id: int, suggestion_index: int, value: str
) -> dict[str, Any]:
    if value not in FEEDBACK_VALUES:
        raise FeedbackInvalid(f"value must be one of {FEEDBACK_VALUES}")
    with engine.begin() as conn:
        generation = require_generation(conn, oid)
        row = conn.execute(
            sa.text("SELECT suggestions, prompt_version FROM coach_review WHERE id = :id"),
            {"id": review_id},
        ).mappings().first()
        if row is None or row["prompt_version"] == PENDING:
            raise ReviewNotFound(review_id)
        if not 0 <= suggestion_index < len(row["suggestions"]):
            raise FeedbackInvalid("suggestion_index out of range")
        fid = conn.execute(
            sa.text(
                "INSERT INTO coach_feedback (review_id, suggestion_index, value, data_generation)"
                " VALUES (:r, :i, :v, :g) RETURNING id"
            ),
            {"r": review_id, "i": suggestion_index, "v": value, "g": generation},
        ).scalar_one()
    return {"id": fid, "review_id": review_id, "suggestion_index": suggestion_index,
            "value": value}


def _rate(num: int, den: int) -> str | None:
    if den == 0:
        return None
    return format((Decimal(num) / Decimal(den)).quantize(Decimal("0.01")), "f")


def _money(d: Decimal) -> str:
    return format(d.normalize(), "f")


def ops_stats(conn: Connection, month: date, cap: Decimal | None = None) -> dict[str, Any]:
    """Coach ops panel for the month starting ``month`` (its day is ignored)."""
    start = month.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    window = {"s": start, "e": end}
    calls = conn.execute(
        sa.text(
            "SELECT count(*) AS n, "
            "percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
            "percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95 "
            "FROM llm_call WHERE created_at >= :s AND created_at < :e"
        ),
        window,
    ).mappings().one()
    first = conn.execute(
        sa.text(
            "SELECT count(*) FILTER (WHERE validation_ok) AS ok, count(*) AS n FROM llm_call "
            "WHERE attempt = 1 AND created_at >= :s AND created_at < :e"
        ),
        window,
    ).mappings().one()
    reviews = conn.execute(
        sa.text(
            "SELECT count(*) FILTER (WHERE source = 'template') AS fb, count(*) AS n "
            "FROM coach_review WHERE prompt_version <> 'pending' "
            "AND created_at >= :s AND created_at < :e"
        ),
        window,
    ).mappings().one()
    b = conn.execute(
        sa.text("SELECT cap_eur, spent_eur FROM llm_budget WHERE period_start = :s"), window
    ).mappings().first()
    cap_value = b["cap_eur"] if b else cap
    return {
        "calls": calls["n"],
        "spent_eur": _money(b["spent_eur"] if b else Decimal(0)),
        "cap_eur": _money(cap_value) if cap_value is not None else None,
        "first_try_pass_rate": _rate(first["ok"], first["n"]),
        "fallback_rate": _rate(reviews["fb"], reviews["n"]),
        "p50_ms": calls["p50"],
        "p95_ms": calls["p95"],
    }
