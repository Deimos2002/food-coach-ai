"""Deterministic template suggestions (weekly-coach spec §3.4).

These are the coach's fallback whenever the LLM fails or is skipped, and the
baseline the LLM must beat in evaluation (spec §4). Pure: dict in, dict out,
no I/O. Decimal strings are taken from the fact sheet as-is -- this module
never parses or recomputes them, so there is no float anywhere here.

Sentences are written to satisfy ``app.domain.coach_validator.validate_output``
by construction:
- numbers and names come only from the cited fact (or its swap/contributors);
- "you ate" and disease/alarm words are never written;
- guideline wording is only added when the cited fact carries a ``card_id``;
- "below" wording is only used when the cited fact's own claim is "below";
- every suggestion cites at least one fact, and a ``keep_going`` suggestion
  only cites facts whose claim is "within", "below" or "around" (controller
  ruling, 2026-10-13 coordinator message on Task 6).

Controller ruling (spec A35, 2026-10-13): the validator's forbidden-wording
check runs on the raw text and is **not** exempted for a cited product name.
A catalogue name is untrusted user-entered text (A35), so if a contributor's
or swap's name itself trips the forbidden-word check, this module never
quotes that name -- it refers to it generically ("a logged item" /
"a safer alternative") instead. This is the one place templates deviate from
"use only the cited fact's names": a name that is itself an injection attempt
is not safe to repeat back to the user.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.coach_validator import FORBIDDEN_PATTERNS
from app.services.search import normalize

MAX_SUGGESTIONS = 3
TITLE_MAX = 60
BODY_MAX = 280

_CALM_CLAIMS = ("within", "below", "around")
_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_PATTERNS))


class NoSuggestions(Exception):
    """Raised when a fact sheet has nothing coachable to say, honestly.

    Two real paths reach this:
    - **Empty facts.** The sheet carries no facts at all.
    - **All cannot_tell.** ``week_rules`` produces a ``"cannot_tell"`` claim for a
      rule when there isn't enough coverage to judge it yet (for example a short
      week with fewer than 3 full-coverage days for that nutrient). If every fact
      in the sheet is ``cannot_tell``, there is no exceeds/below condition to coach
      and no within/below/around fact either, so there is nothing a ``keep_going``
      suggestion could honestly cite.

    The validator requires every suggestion -- including ``keep_going`` -- to cite
    at least one fact, and ``keep_going`` specifically needs a fact whose claim is
    "within", "below" or "around". Fabricating a citation would not be honest, so
    this is raised instead of returning a schema-valid but ungrounded output.
    Callers (the coach service) should treat this the same as "insufficient data"
    for that nutrient and skip presenting a review, rather than show hollow praise.
    """

# Priority order per spec §3.4: sugar exceeds, salt exceeds, protein below,
# energy above or below.
_PRIORITY: tuple[tuple[str, str], ...] = (
    ("sugars_daily", "exceeds"),
    ("salt_daily", "exceeds"),
    ("protein_vs_target", "below"),
    ("energy_vs_target", "above"),
    ("energy_vs_target", "below"),
)


def _is_unsafe_name(name: str) -> bool:
    """True if quoting ``name`` verbatim would trip the validator's wording check."""
    return bool(_FORBIDDEN_RE.search(normalize(name)))


def _safe_contributor_name(name: str) -> str:
    return "a logged item" if _is_unsafe_name(name) else name


def _safe_swap_name(name: str) -> str:
    return "a safer alternative" if _is_unsafe_name(name) else name


def _day_count(fact: dict[str, Any]) -> str:
    n = len(fact.get("days", []))
    return f"{n} day" if n == 1 else f"{n} days"


def _fixed_sentence(rule: str, claim: str, fact: dict[str, Any]) -> tuple[str, str]:
    v = fact["values"]
    days = _day_count(fact)
    if rule == "sugars_daily" and claim == "exceeds":
        body = (
            f"Your recorded values exceed {v['threshold']} {v['unit']} of sugar, "
            f"with at least {v['recorded_lower']} {v['unit']} logged over {days}."
        )
        if fact.get("card_id"):
            body += " This is above the WHO guideline."
        return "Cut back on sugar", body
    if rule == "salt_daily" and claim == "exceeds":
        body = (
            f"Your recorded values exceed {v['threshold']} {v['unit']} of salt, "
            f"with at least {v['recorded_lower']} {v['unit']} logged over {days}."
        )
        if fact.get("card_id"):
            body += " This is above the WHO guideline."
        return "Cut back on salt", body
    if rule == "protein_vs_target" and claim == "below":
        # week_rules._target_rule: values = {recorded_lower, recorded_upper, target, unit}.
        body = (
            f"Based on your completed log, your recorded protein was below your "
            f"{v['target']} {v['unit']} target, at most {v['recorded_upper']} "
            f"{v['unit']} a day over {days}."
        )
        return "Add more protein", body
    if rule == "energy_vs_target" and claim in ("above", "below"):
        body = (
            f"Based on your completed log, your recorded energy was {claim} your "
            f"{v['target']} {v['unit']} target, at least {v['recorded_lower']} "
            f"{v['unit']} a day over {days}."
        )
        return "Watch your energy balance", body
    raise ValueError(f"unsupported rule/claim combination: {rule}/{claim}")


def _build_suggestion(rule: str, claim: str, fact: dict[str, Any]) -> dict[str, Any]:
    title, body = _fixed_sentence(rule, claim, fact)
    kind = "habit"
    swap_ids: list[str] = []
    swaps = fact.get("swaps") or []
    if swaps:
        swap = swaps[0]
        contributor = next(
            (c for c in fact.get("contributors", []) if c.get("id") == swap.get("for")),
            None,
        )
        contributor_name = contributor["name"] if contributor else swap["for"]
        body = (
            f"{body} Try {_safe_swap_name(swap['name'])} instead of "
            f"{_safe_contributor_name(contributor_name)}."
        )
        swap_ids = [swap["id"]]
        kind = "swap"
    return {
        "kind": kind,
        "title": title[:TITLE_MAX],
        "body": body[:BODY_MAX],
        "fact_ids": [fact["id"]],
        "swap_ids": swap_ids,
    }


def _keep_going_suggestion(calm_facts: list[dict[str, Any]]) -> dict[str, Any]:
    # No numbers or names here at all, so there is nothing to validate against
    # the cited facts -- citing them is only to satisfy the "keep_going needs
    # within/below/around facts" rule and to let the UI link back to them.
    return {
        "kind": "keep_going",
        "title": "Keep it up",
        "body": "Keep logging - nothing stood out against your targets this week.",
        "fact_ids": [f["id"] for f in calm_facts],
        "swap_ids": [],
    }


def template_suggestions(sheet: dict[str, Any]) -> dict[str, Any]:
    """Build 1-3 deterministic suggestions from a fact sheet (spec §2.6, §3.2).

    Raises :class:`NoSuggestions` if nothing fired and the sheet has no fact
    whose claim is "within", "below" or "around" to cite for a ``keep_going``
    suggestion -- an empty-facts sheet, or (the realistic case) a short week
    where every rule came back "cannot_tell" for lack of coverage. See
    :class:`NoSuggestions` for why this is a raise rather than a degenerate
    ``keep_going``.
    """
    facts = sheet.get("facts", [])
    suggestions: list[dict[str, Any]] = []
    losing = (sheet.get("goal") or {}).get("type") == "lose"
    for rule, claim in _PRIORITY:
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
        if losing and (rule, claim) == ("energy_vs_target", "below"):
            continue  # in line with the goal: nothing to fix
        for fact in facts:
            if len(suggestions) >= MAX_SUGGESTIONS:
                break
            if fact.get("rule") == rule and fact.get("claim") == claim:
                suggestions.append(_build_suggestion(rule, claim, fact))

    if not suggestions:
        calm_facts = [f for f in facts if f.get("claim") in _CALM_CLAIMS]
        if not calm_facts:
            raise NoSuggestions(
                "nothing fired and no within/below/around fact to cite for "
                "keep_going; see NoSuggestions' docstring"
            )
        suggestions.append(_keep_going_suggestion(calm_facts))

    return {"suggestions": suggestions[:MAX_SUGGESTIONS]}
