"""Deterministic validator for LLM coach output (spec §3.3). Pure: no I/O, no float.

Every suggestion is checked only against the facts and swaps it cites, so a valid
number borrowed from another fact is still rejected. Cited contributor and swap names
are removed from the text before the number and wording checks, so a product name is
always treated as data (A35) and digits inside it ("0%") are ignored.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from app.services.search import normalize

KINDS = ("swap", "habit", "keep_going")
MAX_SUGGESTIONS = 3
MAX_TITLE = 60
MAX_BODY = 280

# Matched against ``normalize(text)`` (lowercase, accents stripped) of the RAW text.
# Cited names are NOT exempt: a name containing forbidden wording fails the output,
# which then falls back to the template (fail-closed).
FORBIDDEN_PATTERNS = (
    r"you ate", r"you consumed", r"you drank",
    r"you(?:'ve| have)? (?:had|eaten|drunk|drank|consumed|ate)",
    r"diabet", r"cancer", r"disease", r"\bobes", r"danger", r"\btoxic", r"poison",
    r"\baddict", r"deadly", r"\bkill",
    # "intake"/"apport" read as actual consumption (A.1): say "recorded" instead.
    r"\bintakes?\b", r"\bapports?\b",
)
_FORBIDDEN = re.compile("|".join(FORBIDDEN_PATTERNS))
# Guideline wording needs a cited fact with a card. Agency acronyms are matched
# case-sensitively on the raw text so the pronoun "who" is not mistaken for WHO.
_UNDECLARED = re.compile(r"cooking (?:salt|fat|addition)|undeclared|\bdeclar")
# Matched after cited product names are removed, so "Sugarfree" in a name is not a claim.
_NUTRIENT_WORDS = {
    "energy_vs_target": re.compile(r"\b(?:energy|kcal|calories?)\b"),
    "protein_vs_target": re.compile(r"\bproteins?\b"),
    "sugars_daily": re.compile(r"\bsugars?\b"),
    "salt_daily": re.compile(r"\b(?:salt|sodium)\b"),
}
_GUIDELINE_WORDS = re.compile(r"guideline|recommend")
_GUIDELINE_BODIES = re.compile(r"\b(?:WHO|EFSA|ANSES)\b")
# Heuristic: any of these words is read as "the amount stayed within/below".
_DIRECTION = re.compile(
    r"\b(?:within|below|under|beneath|less than|lower than|fewer than)\b"
    r"|(?:not|n't) exceed"
)
# Number words (English and French, accent-stripped). Fail-closed: the LLM must use
# digits so every quantity goes through the grounded number check.
_NUMBER_WORDS = re.compile(
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    r"|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty"
    r"|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|half|halves|third|thirds"
    r"|quarters?|twice|double|triple"
    r"|deux|trois|quatre|cinq|sept|huit|neuf|dix|onze|douze|treize|quatorze"
    r"|quinze|seize|vingt|trente|quarante|cinquante|soixante|cents?|mille|moitie"
    r"|tiers|quarts?)\b"
)
# "one/un/une" are ordinary words; reject them only before a unit or serving noun.
_ONE_WITH_UNIT = re.compile(
    r"\b(?:one|un|une)\s+(?:g|kg|ml|cl|l|gram\w*|gramme\w*|can|cans|canette\w*|portion\w*"
    r"|serving\w*|bottle\w*|bouteille\w*|glass\w*|verre\w*)\b"
)
_ROMAN_WITH_UNIT = re.compile(r"\b[IVXLCDM]{2,}\s*(?:g|kg|kcal|ml|%)")
# Second wording pass on text with spaces/punctuation removed ("dia-betes", "D I A B E T E S").
# Only multi-word phrases and words of 6+ letters: short words ("kill", "obes") would match
# across word boundaries ("skillet", "also best"), so they stay on the normal pass only.
_FORBIDDEN_SPACELESS = re.compile(
    r"youate|youvehad|youhaveeaten|youconsumed|youdrank|diabet|cancer"
    r"|poison|danger|deadly|disease"
)
# Common Cyrillic/Greek letters that look like Latin ones.
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ј": "j", "ѕ": "s", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "ԁ": "d",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X", "І": "I",
    "К": "K", "М": "M", "Т": "T", "В": "B", "Н": "H",
    "ο": "o", "α": "a", "ε": "e", "ρ": "p", "ι": "i", "κ": "k", "ν": "v", "τ": "t",
    "υ": "u", "χ": "x", "Ο": "O", "Α": "A", "Ε": "E", "Ρ": "P", "Ι": "I", "Κ": "K",
    "Ν": "N", "Τ": "T", "Χ": "X", "Β": "B", "Η": "H", "Μ": "M", "Ζ": "Z", "Υ": "Y",
})


def canon(text: str) -> str:
    """NFKC, drop format chars (Cf, e.g. zero-width), map Cyrillic/Greek confusables."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.translate(_CONFUSABLES)


def spaceless(text: str) -> str:
    return re.sub(r"[\W_]+", "", normalize(text))


_RANGE = re.compile(r"\d+(?:[.,]\d+)?\s*[-–—]\s*\d+")
# Uncited-name fuzzy check: every adjacent token pair of an uncited catalogue name,
# plus its first token when distinctive (len >= 4 and not a common food word).
_COMMON_WORDS = frozenset({
    "salted", "sweet", "plain", "whole", "light", "fresh", "organic", "natural",
    "homemade", "classic", "original", "mini", "extra", "chocolate", "white", "green",
    "black", "brown", "semi", "skimmed", "sugar", "sugars", "salt", "water", "juice",
    "bread", "milk", "with", "sans", "avec", "pain", "lait", "the", "and",
})
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass(frozen=True)
class ValidationError:
    code: str  # schema | unknown_ref | number | name | wording | direction
    detail: str


def _norm_number(d: Decimal) -> str:
    text = format(d.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _parse_decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        d = Decimal(value)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _rounded(d: Decimal) -> set[str]:
    return {
        _norm_number(d.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        _norm_number(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
    }


def _values_numbers(values: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(values, dict):
        for v in values.values():
            d = _parse_decimal(v)
            if d is not None:
                out |= _rounded(d)
    return out


def _day_of_month(iso: Any) -> str | None:
    if isinstance(iso, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return str(int(iso[8:10]))
    return None


def _check_schema(data: Any) -> list[ValidationError]:
    if not isinstance(data, dict) or set(data) != {"suggestions"}:
        return [ValidationError("schema", "top level must be an object with only 'suggestions'")]
    sugg = data["suggestions"]
    if not isinstance(sugg, list) or not 1 <= len(sugg) <= MAX_SUGGESTIONS:
        return [ValidationError("schema", f"need 1-{MAX_SUGGESTIONS} suggestions")]
    errors: list[ValidationError] = []
    allowed_keys = {"kind", "title", "body", "fact_ids", "swap_ids", "source_ids"}
    for i, s in enumerate(sugg):
        if not isinstance(s, dict) or not set(s) <= allowed_keys or not {
            "kind", "title", "body", "fact_ids"
        } <= set(s):
            errors.append(ValidationError("schema", f"suggestion {i}: wrong keys"))
            continue
        if s["kind"] not in KINDS:
            errors.append(ValidationError("schema", f"suggestion {i}: bad kind"))
        if not isinstance(s["title"], str) or not 0 < len(s["title"]) <= MAX_TITLE:
            errors.append(ValidationError("schema", f"suggestion {i}: title length"))
        if not isinstance(s["body"], str) or not 0 < len(s["body"]) <= MAX_BODY:
            errors.append(ValidationError("schema", f"suggestion {i}: body length"))
        for key in ("fact_ids", "swap_ids", "source_ids"):
            ids = s.get(key, [])
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                errors.append(ValidationError("schema", f"suggestion {i}: {key} must be strings"))
    return errors


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", normalize(canon(text)))}


def uses_passage(body: str, passage: dict) -> bool:
    """A paraphrase cannot be proven faithful, but a body that shares no words with a
    passage is not based on it. Such a citation is dropped (``service.prioritise``), and its
    numbers are not allowed in the body."""
    return len(_content_words(body) & _content_words(str(passage.get("text", "")))) >= 2


def _strip_names(text: str, names: list[str]) -> str:
    # Longest first so a shorter cited name never leaves a fragment of a longer one.
    for name in sorted(names, key=len, reverse=True):
        if name.strip():
            text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def _check_suggestion(
    i: int, s: dict, facts: dict[str, dict], swaps: dict[str, dict], catalogue: list[str],
    knowledge: dict[str, dict] | None = None, goal: str | None = None,
) -> list[ValidationError]:
    errors: list[ValidationError] = []
    knowledge = knowledge or {}
    cited_sources = []
    for kid in s.get("source_ids", []):
        source = knowledge.get(kid)
        if source is None or source.get("for") not in s["fact_ids"]:
            errors.append(ValidationError(
                "unknown_ref", f"suggestion {i}: {kid} is not a passage for a cited fact"))
        elif uses_passage(s["body"], source):
            cited_sources.append(source)
    fact_ids = s["fact_ids"]
    swap_ids = s.get("swap_ids", [])
    for fid in fact_ids:
        if fid not in facts:
            errors.append(ValidationError("unknown_ref", f"suggestion {i}: unknown fact {fid}"))
    for sid in swap_ids:
        if sid not in swaps:
            errors.append(ValidationError("unknown_ref", f"suggestion {i}: unknown swap {sid}"))
    if s["kind"] == "swap" and not swap_ids:
        errors.append(ValidationError("unknown_ref", f"suggestion {i}: kind=swap needs swap_ids"))
    if s["kind"] != "swap" and swap_ids:
        errors.append(ValidationError("unknown_ref", f"suggestion {i}: swap_ids only with kind=swap"))
    # A swap may be cited without its parent fact, but every suggestion cites a fact.
    if not fact_ids:
        errors.append(ValidationError("unknown_ref", f"suggestion {i}: must cite a fact"))
    if s["kind"] == "keep_going" and any(
        facts[f].get("claim") not in ("within", "below", "around") for f in fact_ids if f in facts
    ):
        errors.append(
            ValidationError("direction", f"suggestion {i}: keep_going needs within/below/around facts")
        )

    cited_facts = [facts[f] for f in fact_ids if f in facts]
    cited_swaps = [swaps[x] for x in swap_ids if x in swaps]

    allowed: set[str] = set()
    names: list[str] = []
    for f in cited_facts:
        allowed |= _values_numbers(f.get("values"))
        days = f.get("days") or []
        allowed.add(str(len(days)))
        for day in days:
            dom = _day_of_month(day)
            if dom is not None:
                allowed.add(dom)
        for c in f.get("contributors") or []:
            names.append(_canon(str(c.get("name", ""))))
            for bound in c.get("share") or []:
                d = _parse_decimal(bound)
                if d is not None:
                    allowed.add(_norm_number((d * 100).quantize(Decimal(1), ROUND_HALF_UP)))
    for sw in cited_swaps:
        allowed |= _values_numbers(sw.get("values"))
        # "per 100 ml": the basis in a swap's unit ("g/100ml") is grounded, not invented.
        allowed |= set(re.findall(r"\d+", str((sw.get("values") or {}).get("unit", ""))))
        names.append(_canon(str(sw.get("name", ""))))

    for source in cited_sources:
        for m in _NUMBER.finditer(_canon(str(source.get("text", "")))):
            d = _parse_decimal(m.group(0).lstrip("-").replace(",", "."))
            if d is not None:
                allowed.add(_norm_number(d))

    original = f"{s['title']}\n{s['body']}"
    raw_text = _canon(original)
    text = _strip_names(raw_text, names)

    # Numbers.
    for m in _NUMBER.finditer(text):
        token = m.group(0)
        start = m.start()
        if token.startswith("-") and start > 0 and text[start - 1].isalnum():
            token = token[1:]  # "38-41": a range dash, not a minus sign
        d = _parse_decimal(token.replace(",", "."))
        if d is None or _norm_number(d) not in allowed:
            errors.append(ValidationError("number", f"suggestion {i}: {m.group(0)!r} not in cited facts"))

    norm_stripped = normalize(text)
    for m in _NUMBER_WORDS.finditer(norm_stripped):
        errors.append(ValidationError("number", f"suggestion {i}: number word {m.group(0)!r}"))
    # Checked before NFKC, which would turn fullwidth digits into ASCII ones.
    for ch in original:
        if ch not in "0123456789" and unicodedata.numeric(ch, None) is not None:
            errors.append(ValidationError("number", f"suggestion {i}: non-ASCII numeral {ch!r}"))
    for m in _ONE_WITH_UNIT.finditer(norm_stripped):
        errors.append(ValidationError("number", f"suggestion {i}: number {m.group(0)!r}"))
    for m in _ROMAN_WITH_UNIT.finditer(text):
        errors.append(ValidationError("number", f"suggestion {i}: roman numeral {m.group(0)!r}"))
    for m in _RANGE.finditer(text):
        errors.append(ValidationError("number", f"suggestion {i}: range {m.group(0)!r}"))

    # Names: any catalogue name left after removing cited names is uncited.
    cited_norm = {normalize(n) for n in names}
    norm_text = " " + normalize(_strip_names(normalize(raw_text), list(cited_norm))) + " "
    spaceless_text = _spaceless(norm_text)
    for cat in catalogue:
        n = normalize(_canon(cat))
        if not n or n in cited_norm:
            continue
        tokens = n.split()
        probes = {n} | {f"{a} {b}" for a, b in pairwise(tokens)}
        if len(tokens[0]) >= 4 and tokens[0] not in _COMMON_WORDS:
            probes.add(tokens[0])
        # Also compare with spaces/punctuation removed ("RedBull", "Red-Bull").
        if any(
            re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", norm_text)
            or (len(_spaceless(p)) >= 4 and _spaceless(p) in spaceless_text)
            for p in probes
        ):
            errors.append(ValidationError("name", f"suggestion {i}: uncited product {cat!r}"))

    # Wording: on the RAW text (cited names are not exempt), accent-insensitive.
    norm_raw = normalize(raw_text)
    for m in _FORBIDDEN_SPACELESS.finditer(_spaceless(raw_text)):
        errors.append(ValidationError("wording", f"suggestion {i}: forbidden wording {m.group(0)!r}"))
    for m in _FORBIDDEN.finditer(norm_raw):
        errors.append(ValidationError("wording", f"suggestion {i}: forbidden wording {m.group(0)!r}"))
    if not any(f.get("card_id") for f in cited_facts):
        for m in (*_GUIDELINE_WORDS.finditer(norm_raw), *_GUIDELINE_BODIES.finditer(raw_text)):
            errors.append(
                ValidationError("wording", f"suggestion {i}: {m.group(0)!r} needs a fact with a card")
            )

    # Advice may not treat energy that is in line with the goal as something to fix.
    if s["kind"] != "keep_going" and any(
        f.get("rule") == "energy_vs_target"
        and (f.get("claim"), goal) in {("below", "lose"), ("above", "gain")}
        for f in cited_facts
    ):
        errors.append(ValidationError(
            "direction", f"suggestion {i}: energy is in line with the goal, not a problem"))

    # A nutrient the week has no judged fact about may not be discussed at all. (Requiring
    # the fact to be cited was tried and rejected whole reviews for a passing mention.)
    judged = {f.get("rule") for f in facts.values() if f.get("claim") != "cannot_tell"}
    for rule, pattern in _NUTRIENT_WORDS.items():
        m = pattern.search(norm_stripped)
        if m and rule not in judged:
            errors.append(ValidationError(
                "wording",
                f"suggestion {i}: it mentions {m.group(0)!r} but the sheet has no fact about "
                "it: remove that word from the title and body"))

    # A catalogue swap for the food the advice is about must be offered, not ignored.
    if s["kind"] == "habit":
        for f in cited_facts:
            wanted = {sw.get("for") for sw in f.get("swaps") or []}
            for c in f.get("contributors") or []:
                name = normalize(_canon(str(c.get("name", ""))))
                if c.get("id") in wanted and name and name in norm_raw:
                    errors.append(ValidationError(
                        "unknown_ref",
                        f"suggestion {i}: {c['name']!r} has a swap: use kind=swap and cite it"))

    # Undeclared cooking additions may only be mentioned where a cited fact lists some.
    if _UNDECLARED.search(norm_raw) and not any(f.get("additions_undeclared") for f in cited_facts):
        errors.append(ValidationError(
            "wording", f"suggestion {i}: no cited fact has undeclared cooking additions"))

    # Direction.
    if _DIRECTION.search(norm_raw) and not any(f.get("claim") in ("within", "below") for f in cited_facts):
        errors.append(
            ValidationError("direction", f"suggestion {i}: within/below wording needs such a fact")
        )
    return errors


def validate_output(
    raw: str | bytes | dict, sheet: dict, catalogue_names: list[str]
) -> tuple[dict | None, list[ValidationError]]:
    """Return ``(parsed, [])`` when the output is grounded, else ``(None, errors)``."""
    if isinstance(raw, dict):
        data: Any = raw
    else:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        fenced = _FENCE.fullmatch(raw.strip()) if isinstance(raw, str) else None
        if fenced:
            raw = fenced.group(1)
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            return None, [ValidationError("schema", f"invalid JSON: {exc}")]
    errors = _check_schema(data)
    if errors:
        return None, errors

    facts = {f["id"]: f for f in sheet.get("facts", []) if isinstance(f, dict) and "id" in f}
    swaps = {
        sw["id"]: sw
        for f in facts.values()
        for sw in f.get("swaps") or []
        if isinstance(sw, dict) and "id" in sw
    }
    knowledge = {k["id"]: k for k in sheet.get("knowledge") or [] if isinstance(k, dict)}
    goal = (sheet.get("goal") or {}).get("type")
    for i, s in enumerate(data["suggestions"]):
        errors.extend(_check_suggestion(
            i, s, facts, swaps, list(catalogue_names), knowledge, goal))
    return (None, errors) if errors else (data, [])


# Private aliases kept for existing callers.
_canon = canon
_spaceless = spaceless
