"""Deterministic catalogue swaps (spec §2.5).

Pure domain logic, no I/O. The lookup never guesses: both values must be known with a
finite upper bound, and nothing is offered across basis units.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.domain.quantity import Unit
from app.domain.values import NutrientValue
from app.services.search import normalize

VARIANT_WORDS = [
    "sans sucres",
    "sans sucre",
    "zero",
    "zéro",
    "0%",
    "light",
    "allégé",
    "sugarfree",
    "sugar free",
    "diet",
    "réduit en sel",
    "sans sel ajouté",
]

_SEVENTY_PCT = Decimal("0.7")

# "0% sucres"/"0% sucre" ("0% sugars") is the same claim as "sans sucres" ("no added
# sugars") on French labels; matched as a unit so "0%" doesn't leave a stray "sucres".
_MATCH_PHRASES = [*VARIANT_WORDS, "0% sucres", "0% sucre"]

# Longest phrases first, so a multi-word variant phrase is removed whole rather than
# leaving a stray word behind if a shorter phrase happened to match first.
_SORTED_VARIANT_WORDS = sorted((normalize(w) for w in _MATCH_PHRASES), key=len, reverse=True)

# Token-aware: a variant word is only removed when it isn't glued to other word/percent
# characters, so "zero" inside "zerotti" is left alone, while "0%" still matches on its
# own boundaries.
_VARIANT_PATTERN = re.compile(
    "(?<![\\w%])(?:" + "|".join(re.escape(w) for w in _SORTED_VARIANT_WORDS) + ")(?![\\w%])"
)


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, drop whole-token variant words and collapse whitespace."""
    normalized = normalize(name)
    stripped = _VARIANT_PATTERN.sub(" ", normalized)
    return " ".join(stripped.split())


@dataclass(frozen=True)
class SwapCandidate:
    product_id: int
    name: str
    brand: str | None
    basis_unit: Unit
    value: NutrientValue


def _normalized_brand(brand: str | None) -> str | None:
    return None if brand is None else normalize(brand)


def find_swaps(
    target: SwapCandidate, candidates: list[SwapCandidate], max_n: int = 2
) -> list[SwapCandidate]:
    """Up to ``max_n`` deterministic variant swaps for ``target``, cheapest first."""
    target_brand = _normalized_brand(target.brand)
    target_name = normalize_name(target.name)

    if not target.value.known or target.value.range is None:
        return []
    target_lower = target.value.range.lower

    results: list[SwapCandidate] = []
    for candidate in candidates:
        if candidate.product_id == target.product_id:
            continue
        candidate_brand = _normalized_brand(candidate.brand)
        if candidate_brand != target_brand:
            continue
        if normalize_name(candidate.name) != target_name:
            continue
        if candidate.basis_unit != target.basis_unit:
            continue
        if not candidate.value.known or candidate.value.range is None:
            continue
        candidate_upper = candidate.value.range.upper
        if candidate_upper is None:
            continue
        if candidate_upper <= target_lower * _SEVENTY_PCT:
            results.append(candidate)

    results.sort(key=lambda c: (c.value.range.upper, c.name))
    return results[:max_n]
