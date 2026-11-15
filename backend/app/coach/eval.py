"""Offline eval runner for the weekly coach LLM (weekly-coach spec §4, Task 7b part 2).

Mirrors the service's two-attempt retry (``app.coach.service._call_llm``) closely enough
that the eval measures the same behaviour the controller ships, but it is **not**
region-gated (``app.coach.service.region_allowed``): these are synthetic fact sheets, not
real user weeks, so the EU-datazone gate that protects real traffic does not apply here.
The retry loop is replicated rather than imported because the service's version is coupled
to a live DB connection (``catalogue_names_for`` queries the ``product`` table) and to the
budget/placeholder transaction machinery (phases A/B/C in service.py); this module has
neither a database nor a budget, only a committed, static catalogue
(``evals/coach/catalogue_decoys.json`` plus each sheet's own contributor/swap names). The
message text sent on retry is identical to the service's, so a prompt change is evaluated
the same way it will run in production.

Commands (see ``main``):
    python -m app.coach.eval --split dev|test --source llm|template [--replay] [--record]
    python -m app.coach.eval --rubric <llm_run.jsonl> <template_run.jsonl> [--scores-from scores.csv]
    python -m app.coach.eval --compare <llm_run.jsonl> <template_run.jsonl>         --rubric-scores <llm_run.jsonl.scores.json>         --template-rubric-scores <template_run.jsonl.scores.json>
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from random import Random
from typing import Any

from app.coach.knowledge import Embed, attach_knowledge, query_embedder
from app.coach.llm import (
    FakeLlmClient,
    LlmClient,
    LlmContentFiltered,
    LlmError,
    LlmRateLimited,
    LlmResult,
    cost_eur,
)
from app.coach.service import (
    MAX_TOKENS,
    OUTPUT_SCHEMA,
    TEMPERATURE,
    load_prompt,
    prices_set,
    prioritise,
    strip_fence,
)
from app.config import Settings
from app.domain.coach_templates import NoSuggestions, template_suggestions
from app.domain.coach_validator import validate_output

EVAL_ROOT = Path(__file__).resolve().parents[2] / "evals" / "coach"
RUNS_DIR_NAME = "runs"
RECORDED_DIR_NAME = "recorded"
RECORDED_RUNS_DIR_NAME = "recorded_runs"
REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_REPORT_PATH = REPO_ROOT / "docs" / "evals" / "coach-gate.md"

RUBRIC_CRITERIA = ("specificity", "actionability", "goal_fit", "tone")

# Mirrors spec §4.4 exactly: criterion -> (comparator, threshold). "le"/"ge" read as
# "actual must be <= threshold" / "actual must be >= threshold".
GATE_THRESHOLDS: dict[str, tuple[str, Any]] = {
    "hard_failures": ("le", 0),
    "catch_rate": ("ge", Decimal("1.0")),
    "first_try_pass_rate": ("ge", Decimal("0.90")),
    "rubric_delta": ("ge", Decimal("0.5")),
    "cost_eur_per_review": ("le", Decimal("0.05")),
    "latency_p95_ms": ("le", 15000),
}
GATE_LABELS = {
    "hard_failures": "Hard failures after validation",
    "catch_rate": "Validator catch rate (seeded bad outputs)",
    "first_try_pass_rate": "First-try pass rate",
    "rubric_delta": "Mean rubric, LLM minus template",
    "cost_eur_per_review": "Cost per review",
    "latency_p95_ms": "Latency p95",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_of(path: Path) -> str:
    stem = path.stem
    return stem.split("_", 1)[1] if "_" in stem else stem


def load_decoys(eval_root: Path = EVAL_ROOT) -> list[str]:
    return _load_json(eval_root / "catalogue_decoys.json")


def sheet_catalogue(sheet: dict, decoys: list[str]) -> list[str]:
    """Catalogue names for the offline name check: the sheet's own contributor and swap
    names (so a cited name is never falsely flagged) plus the fixed decoy list (so the
    name check is actually exercised against *something* uncited, even on a sheet whose
    own output names everything it mentions)."""
    names: list[str] = []
    for fact in sheet.get("facts", []):
        for c in fact.get("contributors") or []:
            names.append(c["name"])
        for s in fact.get("swaps") or []:
            names.append(s["name"])
    return names + list(decoys)


def seeded_catch_rate(eval_root: Path = EVAL_ROOT) -> Decimal:
    """Fraction of ``seeded_bad/`` cases correctly rejected with at least their expected
    codes (same contract as ``test_seeded_bad_all_caught``)."""
    paths = sorted((eval_root / "seeded_bad").glob("*.json"))
    if not paths:
        return Decimal(1)
    catalogue = sorted({
        c["name"] for p in (eval_root / "dev").glob("*.json")
        for f in _load_json(p)["facts"] for c in [*f["contributors"], *f["swaps"]]
    })
    caught = 0
    for path in paths:
        bad = _load_json(path)
        sheet = _load_json(eval_root / bad["sheet_ref"])
        parsed, errors = validate_output(bad["output"], sheet, catalogue)
        codes = {e.code for e in errors}
        if parsed is None and set(bad["expected_codes"]) <= codes:
            caught += 1
    return (Decimal(caught) / Decimal(len(paths))).quantize(Decimal("0.01"))


# --- retry (mirrors app.coach.service._call_llm; see module docstring) --------------------


@dataclass
class Attempt:
    attempt: int
    ok: bool | None
    text: str | None
    result: LlmResult | None
    errors: list[dict] | None
    error: str | None = None
    content_filter_categories: frozenset[str] = frozenset()


# A 429 never counts as a validation attempt: wait and retry the same call, up to this
# many waits, before giving up and marking the whole record "rate_limited".
RATE_LIMIT_MAX_WAITS = 6
RATE_LIMIT_DEFAULT_WAIT_SECONDS = 20.0


class _RateLimitExhausted(Exception):
    """Internal signal: ``RATE_LIMIT_MAX_WAITS`` 429s in a row for one call."""


def _complete_with_rate_limit_wait(
    client: LlmClient, sleep_fn: Any, **kwargs: Any
) -> LlmResult:
    for _ in range(RATE_LIMIT_MAX_WAITS):
        try:
            return client.complete(**kwargs)
        except LlmRateLimited as exc:
            sleep_fn(exc.retry_after_seconds or RATE_LIMIT_DEFAULT_WAIT_SECONDS)
    raise _RateLimitExhausted()


def run_retrying(
    client: LlmClient, system: str, view: dict, catalogue: list[str],
    max_tokens: int = MAX_TOKENS, temperature: float = TEMPERATURE,
    sleep_fn: Any = time.sleep,
) -> tuple[list | None, list[Attempt], bool]:
    """Up to two attempts, exactly like the service: the second carries the first
    attempt's validator errors. Returns (suggestions or None, attempts, rate_limited).

    A 429 (``LlmRateLimited``) never counts as one of the two attempts and never triggers
    the template fallback by itself: ``sleep_fn`` is called and the same call is retried,
    up to ``RATE_LIMIT_MAX_WAITS`` times, before giving up and reporting
    ``rate_limited=True``.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": json.dumps(view, ensure_ascii=False)}
    ]
    attempts: list[Attempt] = []
    for attempt in (1, 2):
        try:
            result = _complete_with_rate_limit_wait(
                client, sleep_fn,
                system=system, messages=list(messages), json_schema=OUTPUT_SCHEMA,
                max_tokens=max_tokens, temperature=temperature,
            )
        except _RateLimitExhausted:
            return None, attempts, True
        except LlmContentFiltered as exc:
            attempts.append(
                Attempt(attempt, None, None, None, None, type(exc).__name__, exc.categories)
            )
            return None, attempts, False
        except LlmError as exc:
            attempts.append(Attempt(attempt, None, None, None, None, type(exc).__name__))
            return None, attempts, False
        text = strip_fence(result.text or "")
        parsed, errors = validate_output(text, view, catalogue)
        err_json = [{"code": e.code, "detail": e.detail} for e in errors]
        attempts.append(Attempt(attempt, parsed is not None, result.text, result, err_json or None))
        if parsed is not None:
            return prioritise(parsed["suggestions"], view), attempts, False
        messages = [
            *messages,
            {"role": "assistant", "content": result.text or ""},
            {
                "role": "user",
                "content": "Your output failed the checker. Fix every error and return only "
                "the corrected JSON.\n" + "\n".join(f"- {e['code']}: {e['detail']}" for e in err_json),
            },
        ]
    return None, attempts, False


# --- recorded responses ---------------------------------------------------------------------


def _recorded_path(eval_root: Path, split: str, name: str) -> Path:
    return eval_root / RECORDED_DIR_NAME / split / f"{name}.json"


def load_recorded(eval_root: Path, split: str, name: str) -> list[str | dict]:
    """Loads a recorded response fixture from the committed ``recorded/`` tree under
    ``eval_root`` (same contract as before). ``load_from_dir`` is the replay-dir-aware
    counterpart used by the eval runner."""
    return _load_json(_recorded_path(eval_root, split, name))


def load_from_dir(replay_dir: Path, split: str, name: str) -> list[str | dict]:
    return _load_json(replay_dir / split / f"{name}.json")


def _record_dir(eval_root: Path, split: str, now: str | None = None) -> Path:
    """A fresh, never-committed directory for ``--record`` output, so recording a run
    never overwrites the committed replay fixtures the tests depend on."""
    ts = now or _timestamp()
    return eval_root / RECORDED_RUNS_DIR_NAME / f"{ts}-{split}"


def save_recorded(record_dir: Path, split: str, name: str, texts: list[str | dict]) -> None:
    path = record_dir / split / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(texts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- per-sheet evaluation --------------------------------------------------------------------


def _evaluate_sheet(
    path: Path, split_name: str, source: str, decoys: list[str], *,
    client: LlmClient | None, replay: bool, record: bool, eval_root: Path, settings: Settings,
    sleep_fn: Any = time.sleep, replay_dir: Path | None = None, record_dir: Path | None = None,
    knowledge: bool = True, embed: Embed | None = None,
) -> dict[str, Any]:
    sheet = _load_json(path)
    case = _case_of(path)
    catalogue = sheet_catalogue(sheet, decoys)
    rec: dict[str, Any] = {
        "sheet": f"{split_name}/{path.name}", "case": case, "source": source,
    }

    # A36: fewer than 3 complete days never reaches the LLM in the service either.
    if case == "insufficient_data":
        rec["status"] = "skipped_insufficient_data"
        return rec

    try:
        template = template_suggestions(sheet)["suggestions"]
    except NoSuggestions:
        # Nothing coachable to say, honestly: the service never calls the LLM for this
        # either (service.py: `except NoSuggestions: return store([], "none")`).
        rec["status"] = "skipped_no_suggestions"
        return rec

    if source == "template":
        output = {"suggestions": template}
        # Templates are built correct by construction (coach_templates.py docstring);
        # they are never run through the LLM-output validator in production, so a hard
        # failure here is always 0, never a meaningful signal.
        rec.update(
            status="ok", fallback=False, first_try_ok=True, hard_failure=False,
            tokens_in=0, tokens_out=0, cost_eur="0", latency_ms=0, output=output,
            called_llm=False,
        )
        return rec

    # source == "llm"
    name = path.stem
    if replay:
        texts = load_from_dir(replay_dir or (eval_root / RECORDED_DIR_NAME), split_name, name)
        sheet_client: LlmClient = FakeLlmClient(responses=list(texts))
    else:
        if client is None:
            raise ValueError("a client is required when replay=False")
        sheet_client = client

    system = load_prompt()
    # The passages the coach was shown; an offline replay ranks them by keywords only.
    view = attach_knowledge(sheet, embed) if knowledge else sheet
    suggestions, attempts, rate_limited = run_retrying(
        sheet_client, system, view, catalogue, sleep_fn=sleep_fn,
    )

    if record and not replay:
        save_recorded(
            record_dir or _record_dir(eval_root, split_name), split_name, name,
            [
                {"content_filter": sorted(a.content_filter_categories)}
                if a.error == "LlmContentFiltered" else a.text
                for a in attempts
                if a.text is not None or a.error == "LlmContentFiltered"
            ],
        )

    # The validator's error codes and details for every attempt, so a failed first try
    # can be diagnosed straight from the JSONL without re-running anything.
    rec["attempt_errors"] = [a.errors for a in attempts]

    if rate_limited:
        # Never scored as a failed attempt or a fallback: the request never completed, so
        # it must not touch first_try_pass_rate, cost or latency.
        rec["status"] = "rate_limited"
        return rec

    if attempts and attempts[-1].error == "LlmContentFiltered":
        # Azure's Prompt Shields correctly blocked this request: a safe, expected
        # outcome, not an LLM failure. It must never count against first_try_pass_rate,
        # and never touch latency/token metrics (no completion happened), nor be a hard
        # failure: the template is shown instead.
        rec.update(
            status="content_filtered",
            categories=sorted(attempts[-1].content_filter_categories),
            output={"suggestions": []},
            hard_failure=False,
        )
        return rec

    first_try_ok = bool(attempts) and bool(attempts[0].ok)
    if suggestions is not None:
        output = {"suggestions": suggestions}
        fallback = False
        # Sanity check only: a parsed attempt has already passed this exact validator
        # call, so this is always True by construction (spec: "always 0 for template
        # fallbacks"; here, equally, a non-fallback LLM output cannot fail it either).
        ok = validate_output(output, view, catalogue)[0] is not None
    else:
        output = {"suggestions": []}  # no static fallback: the user is told none passed
        fallback = True
        ok = True

    tokens_in = sum(a.result.input_tokens for a in attempts if a.result)
    tokens_out = sum(a.result.output_tokens for a in attempts if a.result)
    cost = sum((cost_eur(a.result, settings) for a in attempts if a.result), Decimal(0))
    # Wall time the user waits: every attempt, like cost and tokens.
    latency_ms = sum(a.result.latency_ms for a in attempts if a.result)

    rec.update(
        status="ok", fallback=fallback, first_try_ok=first_try_ok, hard_failure=not ok,
        tokens_in=tokens_in, tokens_out=tokens_out, cost_eur=str(cost), latency_ms=latency_ms,
        output=output, called_llm=bool(attempts),
    )
    return rec


def _percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    frac = k - lo
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _summarize(records: list[dict], source: str, eval_root: Path) -> dict[str, Any]:
    ok = [r for r in records if r["status"] == "ok"]
    n = len(records)
    hard_failures = sum(1 for r in ok if r["hard_failure"])
    first_try = [r for r in ok if "first_try_ok" in r]
    first_try_pass_rate = (
        Decimal(sum(1 for r in first_try if r["first_try_ok"])) / Decimal(len(first_try))
    ).quantize(Decimal("0.01")) if first_try else Decimal(0)
    tokens_in = sum(r.get("tokens_in", 0) for r in ok)
    tokens_out = sum(r.get("tokens_out", 0) for r in ok)
    cost_total = sum((Decimal(r.get("cost_eur", "0")) for r in ok), Decimal(0))
    n_cost = sum(1 for r in ok if source == "llm")
    cost_per_review = (cost_total / n_cost).quantize(Decimal("0.0001")) if n_cost else Decimal(0)
    # Only records where an LLM call actually completed carry a real latency: a
    # 0-latency record that never called the model (template-only, cached from a replay
    # that recorded no timing, etc.) would otherwise drag the percentiles down.
    latencies = sorted(r.get("latency_ms", 0) for r in ok if r.get("called_llm"))
    return {
        "source": source,
        "n": n,
        "skipped_insufficient_data": sum(1 for r in records if r["status"] == "skipped_insufficient_data"),
        "skipped_no_suggestions": sum(1 for r in records if r["status"] == "skipped_no_suggestions"),
        "rate_limited": sum(1 for r in records if r["status"] == "rate_limited"),
        "content_filtered": sum(1 for r in records if r["status"] == "content_filtered"),
        "hard_failures": hard_failures,
        "first_try_pass_rate": str(first_try_pass_rate),
        "seeded_catch_rate": str(seeded_catch_rate(eval_root)),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_eur_per_review": str(cost_per_review),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
    }


def _is_problem(fact: dict) -> bool:
    return fact["claim"] in ("exceeds", "above") or (
        fact["rule"] == "protein_vs_target" and fact["claim"] == "below"
    )


def usefulness(records: list[dict], eval_root: Path) -> dict[str, Any]:
    """Deterministic proxies for what the validator cannot see: is the advice about the
    week's problem, and about the food behind it? Reported, not a gate criterion."""
    kinds: list[str] = []
    with_problem = first = with_top = named = 0
    advice = sourced = 0
    for r in records:
        if r["status"] != "ok":
            continue
        suggestions = r["output"]["suggestions"]
        if not suggestions:
            continue
        kinds += [s["kind"] for s in suggestions]
        advice += sum(s["kind"] != "keep_going" for s in suggestions)
        sourced += sum(s["kind"] != "keep_going" and bool(s.get("source_ids"))
                       for s in suggestions)
        facts = {f["id"]: f for f in _load_json(eval_root / r["sheet"])["facts"]}
        problems = [f for f in facts.values() if _is_problem(f)]
        if not problems:
            continue
        with_problem += 1
        cited_first = [facts[i] for i in suggestions[0]["fact_ids"] if i in facts]
        first += any(_is_problem(f) for f in cited_first)
        # A tie for the largest share counts either food.
        tops = [c["name"].lower() for f in problems for c in f["contributors"]
                if c["share"] == f["contributors"][0]["share"]]
        if tops:
            with_top += 1
            text = " ".join(f"{s['title']} {s['body']}" for s in suggestions).lower()
            named += any(t in text for t in tops)

    def rate(num: int, den: int) -> str | None:
        return str((Decimal(num) / Decimal(den)).quantize(Decimal("0.01"))) if den else None

    return {
        "suggestions": len(kinds),
        "keep_going_share": rate(kinds.count("keep_going"), len(kinds)),
        "problem_first_rate": rate(first, with_problem),
        "top_contributor_named_rate": rate(named, with_top),
        # Share of advice (not keep_going) that cites a guideline passage.
        "sourced_advice_rate": rate(sourced, advice),
    }


def run_eval(
    *, source: str, split: str | None = None, sheets: list[Path] | None = None,
    client: LlmClient | None = None, replay: bool = False, record: bool = False,
    eval_root: Path = EVAL_ROOT, run_path: Path | None = None, settings: Settings | None = None,
    now: str | None = None, sleep_fn: Any = time.sleep, pace_seconds: float = 2.0,
    replay_dir: Path | None = None, knowledge: bool = True, embed: Embed | None = None,
) -> tuple[dict[str, Any], Path]:
    """Run the eval over ``split`` (or an explicit ``sheets`` list, for tests) and write
    the per-sheet JSONL. Returns (summary, run_path).

    ``sleep_fn`` is used both for the 429 wait loop (``run_retrying``) and for the
    ``pace_seconds`` pause between sheets, so tests can inject a no-op and never
    actually sleep.
    """
    if source not in ("llm", "template"):
        raise ValueError("source must be 'llm' or 'template'")
    if sheets is None:
        if split is None:
            raise ValueError("split or sheets is required")
        sheets = sorted((eval_root / split).glob("*.json"))
    decoys = load_decoys(eval_root)
    settings = settings or Settings()
    ts = now or _timestamp()
    record_dir = _record_dir(eval_root, split or "custom", ts) if record and not replay else None

    records = []
    for i, path in enumerate(sheets):
        split_name = split or path.parent.name
        records.append(_evaluate_sheet(
            path, split_name, source, decoys,
            client=client, replay=replay, record=record, eval_root=eval_root, settings=settings,
            sleep_fn=sleep_fn, replay_dir=replay_dir, record_dir=record_dir,
            knowledge=knowledge, embed=embed,
        ))
        if source == "llm" and not replay and pace_seconds and i < len(sheets) - 1:
            sleep_fn(pace_seconds)

    if run_path is None:
        run_dir = eval_root / RUNS_DIR_NAME
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / f"{ts}-{split or 'custom'}-{source}.jsonl"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8",
    )
    summary = _summarize(records, source, eval_root)
    summary["usefulness"] = usefulness(records, eval_root)
    return summary, run_path


def _timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# --- rubric (blind, deterministic, §4.3) -----------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_rubric(
    run_path: Path, seed: int, *, scores_from: Path | None = None, out_path: Path | None = None,
    render_fn: Any = print, input_fn: Any = input,
) -> dict[str, Any]:
    """Deprecated for the gate: scores one run alone, so the scorer knows which source every
    item came from. Use ``run_blind_rubric`` (``--rubric RUN_A RUN_B``) for §4.4.

    Blind 1-5 scoring of a run's shown outputs (spec §4.3).

    Blind: only ``rec["output"]`` (the suggestions actually shown) is rendered; the sheet
    name, case and source are never passed to ``render_fn``/``input_fn``. Deterministic:
    the presentation order is ``Random(seed).shuffle`` of the scorable records, so the same
    seed always produces the same order (and, with ``--scores-from``, the same mapping back
    to sheets) -- required for ``--scores-from`` to be usable in a non-interactive test.
    """
    records = _read_jsonl(run_path)
    scorable = [r for r in records if r.get("status") == "ok"]
    order = list(range(len(scorable)))
    Random(seed).shuffle(order)

    scores: dict[str, dict[str, int]] = {}
    if scores_from is not None:
        with open(scores_from, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for pos, idx in enumerate(order):
            rec = scorable[idx]
            row = rows[pos]
            scores[rec["sheet"]] = {c: int(row[c]) for c in RUBRIC_CRITERIA}
    else:
        for pos, idx in enumerate(order):
            rec = scorable[idx]
            render_fn(f"--- item {pos + 1}/{len(order)} ---")
            render_fn(json.dumps(rec["output"], indent=2, ensure_ascii=False))
            entry: dict[str, int] = {}
            for criterion in RUBRIC_CRITERIA:
                while True:
                    raw = input_fn(f"{criterion} (1-5): ")
                    try:
                        value = int(raw)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= value <= 5:
                        entry[criterion] = value
                        break
            scores[rec["sheet"]] = entry

    out_path = out_path or run_path.with_name(run_path.name + ".scores.json")
    payload = {"seed": seed, "order": order, "scores": scores}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _ask_scores(render_fn: Any, input_fn: Any, pos: int, total: int, output: Any) -> dict[str, int]:
    render_fn(f"--- item {pos + 1}/{total} ---")
    render_fn(json.dumps(output, indent=2, ensure_ascii=False))
    entry: dict[str, int] = {}
    for criterion in RUBRIC_CRITERIA:
        while True:
            raw = input_fn(f"{criterion} (1-5): ")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 5:
                entry[criterion] = value
                break
    return entry


def run_blind_rubric(
    run_a: Path, run_b: Path, seed: int, *, scores_from: Path | None = None,
    render_fn: Any = print, input_fn: Any = input,
) -> dict[str, Any]:
    """Blind 1-5 scoring across two runs (spec §4.3), for the §4.4 gate.

    Both runs' scorable records are merged and shuffled together with ``Random(seed)``, so
    the scorer cannot tell the LLM's outputs from the template's. Only ``output`` is shown:
    never the sheet, case or source. Scores are mapped back by ``(sheet, source)`` and
    written to ``<run>.scores.json`` next to each run. With ``scores_from`` (CSV, one row
    per shown item, in presentation order) the session is non-interactive.
    """
    runs = [run_a, run_b]
    items: list[tuple[int, dict]] = []
    sources: list[str] = []
    for k, path in enumerate(runs):
        recs = [r for r in _read_jsonl(path) if r.get("status") == "ok"]
        found = {r.get("source") for r in recs}
        if len(found) > 1:
            raise ValueError(f"{path} mixes sources {sorted(found)}")
        sources.append(found.pop() if found else f"run{k}")
        items.extend((k, r) for r in recs)
    if sources[0] == sources[1]:
        raise ValueError("the two runs must have different sources")
    order = list(range(len(items)))
    Random(seed).shuffle(order)

    rows: list[dict[str, str]] | None = None
    if scores_from is not None:
        with open(scores_from, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if len(rows) != len(order):
            raise ValueError(f"{scores_from}: {len(rows)} rows for {len(order)} items")

    scores: list[dict[str, dict[str, int]]] = [{}, {}]
    shown: list[list[str]] = []
    for pos, idx in enumerate(order):
        k, rec = items[idx]
        if rows is not None:
            entry = {c: int(rows[pos][c]) for c in RUBRIC_CRITERIA}
        else:
            entry = _ask_scores(render_fn, input_fn, pos, len(order), rec["output"])
        scores[k][rec["sheet"]] = entry
        shown.append([rec["sheet"], sources[k]])

    for k, path in enumerate(runs):
        payload = {"seed": seed, "source": sources[k], "scores": scores[k]}
        path.with_name(path.name + ".scores.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    return {"seed": seed, "order": shown, "scores": {s: scores[k] for k, s in enumerate(sources)}}


def rubric_delta(llm_scores: dict[str, dict[str, int]], template_scores: dict[str, dict[str, int]]) -> Decimal:
    """Mean rubric score, LLM minus template, averaged over all four criteria and every
    sheet scored on both sides."""
    common = set(llm_scores) & set(template_scores)
    if not common:
        return Decimal(0)
    diffs = []
    for sheet in common:
        for criterion in RUBRIC_CRITERIA:
            diffs.append(llm_scores[sheet][criterion] - template_scores[sheet][criterion])
    return (Decimal(sum(diffs)) / Decimal(len(diffs))).quantize(Decimal("0.01"))


# --- gate (§4.4) ---------------------------------------------------------------------------


def _passes(comparator: str, actual: Any, threshold: Any) -> bool:
    if comparator == "le":
        return actual <= threshold
    return actual >= threshold  # "ge"


def gate_table(metrics: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """The §4.4 gate table for ``metrics`` (one value per key in ``GATE_THRESHOLDS``).
    Returns (rows, overall_passed)."""
    rows = []
    for key, (comparator, threshold) in GATE_THRESHOLDS.items():
        actual = metrics[key]
        passed = _passes(comparator, actual, threshold)
        rows.append({
            "criterion": key, "label": GATE_LABELS[key], "threshold": threshold,
            "actual": actual, "passed": passed,
        })
    return rows, all(r["passed"] for r in rows)


def _fmt(value: Any) -> str:
    return str(value)


def render_gate_markdown(rows: list[dict[str, Any]], passed: bool) -> str:
    lines = ["# Weekly coach LLM gate (spec §4.4)", "", "| Criterion | Threshold | Actual | Result |",
              "|---|---|---|---|"]
    for row in rows:
        result = "PASS" if row["passed"] else "FAIL"
        lines.append(f"| {row['label']} | {_fmt(row['threshold'])} | {_fmt(row['actual'])} | {result} |")
    lines.append("")
    lines.append(f"**Decision: {'PASS' if passed else 'FAIL'}** -- "
                 f"{'ships source=llm.' if passed else 'ships template-only.'}")
    lines.append("")
    return "\n".join(lines)


def write_gate_report(
    llm_summary: dict[str, Any], *, rubric_delta: Decimal, out_path: Path = GATE_REPORT_PATH,
) -> bool:
    """Builds the §4.4 metrics from an ``llm`` ``run_eval`` summary plus a rubric delta,
    writes ``out_path`` and returns whether the gate passed."""
    metrics = {
        "hard_failures": llm_summary["hard_failures"],
        "catch_rate": Decimal(llm_summary["seeded_catch_rate"]),
        "first_try_pass_rate": Decimal(llm_summary["first_try_pass_rate"]),
        "rubric_delta": rubric_delta,
        "cost_eur_per_review": Decimal(llm_summary["cost_eur_per_review"]),
        "latency_p95_ms": llm_summary["latency_p95_ms"],
    }
    rows, passed = gate_table(metrics)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_gate_markdown(rows, passed), encoding="utf-8")
    return passed


# --- CLI --------------------------------------------------------------------------------------


def _print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.coach.eval")
    parser.add_argument("--split", choices=("dev", "test", "dev_mixed", "test_mixed"))
    parser.add_argument("--source", choices=("llm", "template"))
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--no-knowledge", action="store_true",
                        help="give the coach no guideline passages (retrieval-off arm)")
    parser.add_argument("--replay-dir", type=Path, default=EVAL_ROOT / RECORDED_DIR_NAME)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--pace-seconds", type=float, default=2.0)
    parser.add_argument("--rubric", nargs=2, type=Path, metavar=("LLM_RUN", "TEMPLATE_RUN"))
    parser.add_argument("--scores-from", type=Path)
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--compare", nargs=2, metavar=("LLM_RUN", "TEMPLATE_RUN"))
    parser.add_argument("--rubric-scores", type=Path)
    parser.add_argument("--template-rubric-scores", type=Path)
    args = parser.parse_args(argv)

    if args.rubric is not None:
        result = run_blind_rubric(args.rubric[0], args.rubric[1], args.seed,
                                  scores_from=args.scores_from)
        print(json.dumps({"seed": result["seed"], "n": len(result["order"])}, indent=2))
        return 0

    if args.compare is not None:
        if not (args.rubric_scores and args.template_rubric_scores):
            parser.error("--compare needs --rubric-scores and --template-rubric-scores")
        llm_run, template_run = (Path(p) for p in args.compare)
        llm_records = _read_jsonl(llm_run)
        for path, records, source in ((llm_run, llm_records, "llm"),
                                      (template_run, _read_jsonl(template_run), "template")):
            if not records or any(
                r.get("source") != source or not str(r.get("sheet", "")).startswith("test/")
                for r in records
            ):
                parser.error(f"{path} must be a --split test --source {source} run")
        llm_summary = _summarize(llm_records, "llm", EVAL_ROOT)
        llm_scores = _load_json(args.rubric_scores)["scores"]
        template_scores = _load_json(args.template_rubric_scores)["scores"]
        delta = rubric_delta(llm_scores, template_scores)
        passed = write_gate_report(llm_summary, rubric_delta=delta)
        print("PASS" if passed else "FAIL")
        return 0 if passed else 1

    if not args.split or not args.source:
        parser.error("--split and --source are required unless --rubric or --compare is given")
    settings = Settings()
    if args.source == "llm" and not prices_set(settings):
        parser.error("COACH_PRICE_IN_PER_1K and COACH_PRICE_OUT_PER_1K must be set (> 0) "
                     "for --source llm, or the cost metric is meaningless")
    client: LlmClient | None = None
    if args.source == "llm" and not args.replay:
        from app.coach.llm import AzureLlmClient

        client = AzureLlmClient(settings)
    summary, run_path = run_eval(
        split=args.split, source=args.source, client=client, replay=args.replay,
        record=args.record, settings=settings, pace_seconds=args.pace_seconds,
        replay_dir=args.replay_dir, knowledge=not args.no_knowledge,
        embed=None if args.replay else query_embedder(settings),
    )
    print(f"wrote {run_path}")
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
