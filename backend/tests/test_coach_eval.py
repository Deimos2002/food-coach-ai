"""Offline contracts for the committed weekly-coach evaluation corpus."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from app.domain.coach_validator import validate_output
from app.domain.week_rules import iso_week_days

ROOT = Path(__file__).resolve().parents[1] / "evals" / "coach"
# Frozen recordings for runner tests (03 fails first try, passes on retry), kept apart
# from evals/coach/recorded/ so re-recording the eval splits cannot change them.
RUNNER_REPLAY = Path(__file__).resolve().parent / "fixtures" / "coach_replay"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fixture_matches_real_llm_view_key_sets():
    from datetime import date
    from decimal import Decimal

    from app.coach.factsheet import _fact_json, assemble_sheet, llm_view
    from app.domain.item_calc import ItemNutrient
    from app.domain.quantity import Unit
    from app.domain.swaps import SwapCandidate
    from app.domain.values import Nutrient, NutrientValue, Qualifier, Range, Source
    from app.domain.week_rules import RuleFact

    day = date(2026, 10, 12)
    value = NutrientValue(True, Range.point(Decimal(80)), Qualifier.NONE, Source.MANUAL)
    target = SwapCandidate(1, "Peach drink", "Brand", Unit.ML, value)
    swap = SwapCandidate(2, "Peach drink Sugarfree", "Brand", Unit.ML, value)
    rule = RuleFact("F1", "sugars_daily", Nutrient.SUGARS, "exceeds", {},
                    (day,), "who_free_sugars", "Recorded sugar exceeds.")
    items = [{"day": day, "product_id": 1, "name": target.name,
              "nutrients": {Nutrient.SUGARS: ItemNutrient(True, value.range, Qualifier.NONE)},
              "unresolved_for": frozenset(), "meal_id": 1, "item_id": 1}]
    # _fact_json does no DB reads with a populated swap cache, and assemble_sheet is the
    # pure top-level assembler build_fact_sheet uses, so both key sets come from code.
    real_top = llm_view(assemble_sheet(
        week="2026-W42", coverage={}, days_included=[], days_missing=[], facts=[],
        goal=None, trend=None,
    ))
    real = llm_view({"facts": [_fact_json(
        None, rule, items, {"C": 0, "S": 0}, {(1, Nutrient.SUGARS): (target, [swap])},
    )]})["facts"][0]
    fixture = load(ROOT / "dev" / "01_sugar_swap.json")
    assert set(fixture) == set(real_top)
    fact = next(f for f in fixture["facts"] if f["rule"] == "sugars_daily")
    assert set(fact) == set(real)
    assert set(fact["contributors"][0]) == set(real["contributors"][0])
    assert set(fact["swaps"][0]) == set(real["swaps"][0])
    assert set(fact["swaps"][0]["values"]) == set(real["swaps"][0]["values"])
    assert "item_refs" not in json.dumps(fixture)


def test_fixture_has_undeclared_dish():
    for split in ("dev", "test"):
        sheets = [load(p) for p in (ROOT / split).glob("*_undeclared_dish.json")]
        assert len(sheets) == 1
        salt = next(f for f in sheets[0]["facts"] if f["rule"] == "salt_daily")
        assert salt["claim"] == "exceeds"
        assert salt["additions_undeclared"] == [salt["contributors"][0]["name"]]
        assert salt["contributors"][0]["additions_undeclared"] is True
        assert salt["contributors"][0]["share"] == ["0.00", None]


def test_fixture_sheets_have_resolvable_ids():
    for split, count in (("dev", 25), ("test", 15)):
        paths = sorted((ROOT / split).glob("*.json"))
        assert len(paths) == count
        for path in paths:
            sheet = load(path)
            assert sheet["schema_version"] == 1
            assert "item_refs" not in json.dumps(sheet)
            unhashed = {k: v for k, v in sheet.items() if k != "hash"}
            canonical = json.dumps(unhashed, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False)
            assert sheet["hash"] == hashlib.sha256(canonical.encode()).hexdigest()
            week = {d.isoformat() for d in iso_week_days(sheet["week"])}
            assert set(sheet["days_included"]) | set(sheet["days_missing"]) == week
            assert not set(sheet["days_included"]) & set(sheet["days_missing"])
            ids = []
            for fact in sheet["facts"]:
                ids.append(fact["id"])
                assert isinstance(fact["additions_undeclared"], list)
                assert set(fact["days"]) <= week
                contributors = {c["id"] for c in fact["contributors"]}
                for contributor in fact["contributors"]:
                    assert isinstance(contributor["additions_undeclared"], bool)
                    assert "product_id" not in contributor
                ids.extend(contributors)
                for swap in fact["swaps"]:
                    ids.append(swap["id"])
                    assert swap["for"] in contributors
                    assert swap["values"]["basis"] == "catalogue_per_100"
                    assert "product_id" not in swap
                assert fact["card_id"] in (None, "who_salt", "who_free_sugars")
            assert len(ids) == len(set(ids))


def test_seeded_bad_all_caught():
    paths = sorted((ROOT / "seeded_bad").glob("*.json"))
    assert len(paths) == 20
    covered = set()
    for path in paths:
        bad = load(path)
        assert set(bad) == {"sheet_ref", "output", "expected_codes"}
        sheet = load(ROOT / bad["sheet_ref"])
        # The corpus itself is the offline catalogue: this also catches a real name
        # borrowed from a different sheet, rather than only invented strings.
        catalogue = sorted({c["name"] for p in (ROOT / "dev").glob("*.json")
                            for f in load(p)["facts"]
                            for c in [*f["contributors"], *f["swaps"]]})
        parsed, errors = validate_output(bad["output"], sheet, catalogue)
        codes = {e.code for e in errors}
        assert parsed is None, path.name
        assert set(bad["expected_codes"]) <= codes, (path.name, errors)
        covered.update(bad["expected_codes"])
    assert covered == {"schema", "unknown_ref", "number", "name", "wording", "direction"}


def test_required_scenarios_have_their_intended_facts():
    for split in ("dev", "test"):
        sheets = {p.stem[3:]: load(p) for p in sorted((ROOT / split).glob("*.json"))}

        def fact(case, rule, sheets=sheets):
            return next(f for f in sheets[case]["facts"] if f["rule"] == rule)

        for case in ("sugar_swap", "sugar_no_swap", "zero_percent_swap"):
            assert fact(case, "sugars_daily")["claim"] == "exceeds"
        assert fact("sugar_swap", "sugars_daily")["swaps"]
        assert not fact("sugar_no_swap", "sugars_daily")["swaps"]
        assert "0%" in fact("zero_percent_swap", "sugars_daily")["swaps"][0]["name"]
        salt = fact("partial_salt", "salt_daily")
        assert salt["claim"] == "exceeds"
        assert set(salt["days"]) <= set(sheets["partial_salt"]["days_included"])
        assert "partial" in sheets["partial_salt"]["coverage"]
        assert fact("protein_below", "protein_vs_target")["claim"] == "below"
        energy = fact("energy_unknown", "energy_vs_target")
        assert energy["claim"] == "cannot_tell"
        # Production emits contributor context only for fired claims. Unknown
        # energy instead manifests as zero full-coverage days and cannot_tell.
        assert energy["values"]["full_coverage_days"] == "0"
        assert not energy["contributors"]
        trace = fact("traces", "sugars_daily")
        assert trace["claim"] == "exceeds"
        assert any(c["share"] == ["0.00", None] for c in trace["contributors"])
        assert len(sheets["insufficient_data"]["days_included"]) == 2
        assert all(f["claim"] in {"within", "around"} for f in sheets["good_week"]["facts"])
        assert sum(f["claim"] in {"above", "below", "exceeds"}
                   for f in sheets["several_facts"]["facts"]) >= 3
        assert "Ignore previous instructions" in fact(
            "injection_name", "sugars_daily",
        )["contributors"][0]["name"]
        assert not fact("non_ascii", "sugars_daily")["contributors"][0]["name"].isascii()
        assert fact("unverified_card", "sugars_daily")["card_id"] is None


def test_regeneration_is_identical_and_splits_have_distinct_names(tmp_path):
    from evals.coach.make_fixtures import SEEDS, write_seeded_bad, write_sheets

    write_sheets(tmp_path)
    write_seeded_bad(tmp_path)
    assert SEEDS["dev"] != SEEDS["test"]
    for path in ROOT.glob("*/*.json"):
        assert path.read_bytes() == (tmp_path / path.relative_to(ROOT)).read_bytes()
    names = {}
    for split in ("dev", "test"):
        names[split] = {c["name"] for p in (ROOT / split).glob("*.json")
                        for f in load(p)["facts"] for c in f["contributors"]}
    assert not names["dev"] & names["test"]


# --- eval runner (Task 7b, part 2) ------------------------------------------------------


def test_template_run_zero_hard_failures_dev():
    from app.coach.eval import run_eval

    summary, run_path = run_eval(split="dev", source="template", eval_root=ROOT)
    assert summary["hard_failures"] == 0
    assert summary["n"] == 25
    assert run_path.exists()
    assert run_path.suffix == ".jsonl"
    lines = run_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 25
    for line in lines:
        json.loads(line)  # each record is valid JSON
    run_path.unlink()


def test_replay_run_matches_recorded():
    from app.coach.eval import run_eval

    sheets = [ROOT / "dev" / "01_sugar_swap.json", ROOT / "dev" / "03_partial_salt.json"]
    summary, run_path = run_eval(
        source="llm", sheets=sheets, split="dev", replay=True, eval_root=ROOT,
        replay_dir=RUNNER_REPLAY,
    )
    try:
        assert summary["n"] == 2
        assert summary["hard_failures"] == 0
        # Sheet 01 passes on the first attempt; sheet 03 only on the retry.
        assert summary["first_try_pass_rate"] == "0.50"
        records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
        by_sheet = {r["sheet"]: r for r in records}
        assert by_sheet["dev/01_sugar_swap.json"]["first_try_ok"] is True
        assert by_sheet["dev/01_sugar_swap.json"]["fallback"] is False
        assert by_sheet["dev/03_partial_salt.json"]["first_try_ok"] is False
        assert by_sheet["dev/03_partial_salt.json"]["fallback"] is False
    finally:
        run_path.unlink()


def test_replay_requires_no_network(monkeypatch):
    """Replay must never touch AzureLlmClient / real sockets (conftest also enforces this)."""
    from app.coach.eval import run_eval

    sheets = [ROOT / "dev" / "01_sugar_swap.json"]
    summary, run_path = run_eval(
        source="llm", sheets=sheets, split="dev", replay=True, eval_root=ROOT,
    )
    run_path.unlink()
    assert summary["n"] == 1


def test_gate_table_thresholds_pass():
    from app.coach.eval import GATE_THRESHOLDS, gate_table

    metrics = {
        "hard_failures": 0,
        "catch_rate": Decimal("1.0"),
        "first_try_pass_rate": Decimal("0.92"),
        "rubric_delta": Decimal("0.6"),
        "cost_eur_per_review": Decimal("0.01"),
        "latency_p95_ms": 3000,
    }
    rows, passed = gate_table(metrics)
    assert passed is True
    assert all(r["passed"] for r in rows)
    assert {r["criterion"] for r in rows} == set(GATE_THRESHOLDS)


def test_gate_table_thresholds_fail():
    from app.coach.eval import gate_table

    metrics = {
        "hard_failures": 1,
        "catch_rate": Decimal("1.0"),
        "first_try_pass_rate": Decimal("0.80"),  # below 0.90 threshold
        "rubric_delta": Decimal("0.6"),
        "cost_eur_per_review": Decimal("0.01"),
        "latency_p95_ms": 3000,
    }
    rows, passed = gate_table(metrics)
    assert passed is False
    failed = {r["criterion"] for r in rows if not r["passed"]}
    assert "hard_failures" in failed
    assert "first_try_pass_rate" in failed
    assert "catch_rate" in {r["criterion"] for r in rows}  # present even when it passes


def test_compare_writes_gate_report(tmp_path):
    from app.coach.eval import write_gate_report

    llm_summary = {
        "hard_failures": 0, "seeded_catch_rate": "1.00", "first_try_pass_rate": "0.95",
        "cost_eur_per_review": "0.01", "latency_p95_ms": 2000,
    }
    out = tmp_path / "coach-gate.md"
    passed = write_gate_report(llm_summary, rubric_delta=Decimal("0.6"), out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "PASS" in text
    assert passed is True
    assert "Hard failures" in text


def test_rubric_is_blind_and_deterministic(tmp_path):
    from app.coach.eval import run_rubric

    records = [
        {"sheet": "dev/01_sugar_swap.json", "case": "sugar_swap", "source": "llm",
         "status": "ok", "output": {"suggestions": [{"title": "Cut sugar"}]}},
        {"sheet": "dev/02_sugar_no_swap.json", "case": "sugar_no_swap", "source": "template",
         "status": "ok", "output": {"suggestions": [{"title": "Cut back on sugar"}]}},
        {"sheet": "dev/03_partial_salt.json", "case": "partial_salt", "source": "llm",
         "status": "ok", "output": {"suggestions": [{"title": "Cut back on salt"}]}},
    ]
    run_path = tmp_path / "run.jsonl"
    run_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    csv_text = (
        "specificity,actionability,goal_fit,tone\n"
        "4,5,3,4\n"
        "2,2,2,2\n"
        "5,4,4,5\n"
    )
    scores_csv = tmp_path / "scores.csv"
    scores_csv.write_text(csv_text, encoding="utf-8")

    rendered: list[str] = []
    result1 = run_rubric(
        run_path, seed=42, scores_from=scores_csv,
        out_path=tmp_path / "run1.scores.json", render_fn=rendered.append,
    )
    result2 = run_rubric(
        run_path, seed=42, scores_from=scores_csv,
        out_path=tmp_path / "run2.scores.json",
    )
    assert result1["scores"] == result2["scores"]
    assert set(result1["scores"]) == {r["sheet"] for r in records}
    # Blind: the rendered view never leaks the case name or the source label.
    blind_text = "\n".join(rendered)
    assert "sugar_swap" not in blind_text
    assert "partial_salt" not in blind_text
    assert "llm" not in blind_text
    assert "template" not in blind_text
    # Deterministic shuffling with a different seed gives a different presentation order.
    order_42 = run_rubric(
        run_path, seed=42, scores_from=scores_csv, out_path=tmp_path / "run3.scores.json",
    )["order"]
    order_7 = run_rubric(
        run_path, seed=7, scores_from=scores_csv, out_path=tmp_path / "run4.scores.json",
    )["order"]
    assert order_42 != order_7


def test_eval_llm_source_refuses_unset_prices(monkeypatch, capsys):
    import pytest

    from app.coach import eval as coach_eval
    from app.config import Settings

    monkeypatch.setattr(coach_eval, "Settings", lambda: Settings(
        coach_price_in_per_1k=Decimal(0), coach_price_out_per_1k=Decimal("0.002"),
    ))
    with pytest.raises(SystemExit) as exc:
        coach_eval.main(["--split", "dev", "--source", "llm", "--replay"])
    assert exc.value.code != 0
    assert "price" in capsys.readouterr().err.lower()


def test_eval_latency_and_cost_sum_all_attempts(tmp_path):
    from app.coach.eval import load_from_dir, run_eval
    from app.coach.llm import LlmResult
    from app.config import Settings

    texts = load_from_dir(RUNNER_REPLAY, "dev", "03_partial_salt")  # invalid, then valid
    latencies = iter([100, 250])

    class TimedClient:
        def complete(self, **_kw):
            return LlmResult(texts.pop(0), 1000, 500, next(latencies), "fake")

    settings = Settings(coach_price_in_per_1k=Decimal("0.001"),
                        coach_price_out_per_1k=Decimal("0.002"))
    _, run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "03_partial_salt.json"], split="dev",
        client=TimedClient(), eval_root=ROOT, run_path=tmp_path / "run.jsonl",
        settings=settings,
    )
    (rec,) = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    assert rec["first_try_ok"] is False and rec["fallback"] is False
    assert rec["latency_ms"] == 350
    assert Decimal(rec["cost_eur"]) == Decimal("0.004")


def _write_run(path, sheets, source, split="test"):
    recs = [{"sheet": f"{split}/{s}", "case": s, "source": source, "status": "ok",
             "fallback": False, "first_try_ok": True, "hard_failure": False,
             "cost_eur": "0.01", "latency_ms": 100,
             "output": {"suggestions": [{"title": "Small change"}]}} for s in sheets]
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def test_blind_rubric_merges_both_runs(tmp_path):
    from app.coach.eval import run_blind_rubric

    sheets = ["01_a.json", "02_b.json"]
    run_llm = _write_run(tmp_path / "llm.jsonl", sheets, "llm")
    run_tpl = _write_run(tmp_path / "tpl.jsonl", sheets, "template")
    rendered: list[str] = []
    answers = iter(str(1 + i % 5) for i in range(16))
    out = run_blind_rubric(run_llm, run_tpl, seed=3, render_fn=rendered.append,
                           input_fn=lambda _p: next(answers))
    text = "\n".join(rendered)
    assert text.count("--- item") == 4  # both runs, merged into one session
    for leak in ("01_a", "02_b", "test/", "llm", "template"):
        assert leak not in text
    a = json.loads((tmp_path / "llm.jsonl.scores.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "tpl.jsonl.scores.json").read_text(encoding="utf-8"))
    assert a["source"] == "llm" and b["source"] == "template"
    assert set(a["scores"]) == set(b["scores"]) == {f"test/{s}" for s in sheets}
    # Scores map back by (sheet, source): rendered item k's scores land on its own record.
    rows = ["specificity,actionability,goal_fit,tone"] + [f"{k},{k},{k},{k}" for k in (1, 2, 3, 4)]
    csv_path = tmp_path / "s.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    shown: list[str] = []
    res = run_blind_rubric(run_llm, run_tpl, seed=3, scores_from=csv_path,
                           render_fn=shown.append)
    assert res["order"] == out["order"]
    files = {"llm": tmp_path / "llm.jsonl.scores.json",
             "template": tmp_path / "tpl.jsonl.scores.json"}
    saved = {k: json.loads(v.read_text(encoding="utf-8"))["scores"] for k, v in files.items()}
    for pos, (sheet, source) in enumerate(res["order"]):
        assert saved[source][sheet]["tone"] == pos + 1

def test_compare_requires_both_scores_and_test_runs(tmp_path, monkeypatch, capsys):
    import pytest

    from app.coach import eval as coach_eval

    sheets = ["01_a.json"]
    llm = _write_run(tmp_path / "llm.jsonl", sheets, "llm")
    tpl = _write_run(tmp_path / "tpl.jsonl", sheets, "template")
    dev_llm = _write_run(tmp_path / "dev_llm.jsonl", sheets, "llm", split="dev")
    scores = tmp_path / "x.scores.json"
    scores.write_text(json.dumps({"scores": {"test/01_a.json": dict.fromkeys(
        ("specificity", "actionability", "goal_fit", "tone"), 3)}}), encoding="utf-8")
    t_scores = tmp_path / "y.scores.json"
    t_scores.write_text(json.dumps({"scores": {"test/01_a.json": dict.fromkeys(
        ("specificity", "actionability", "goal_fit", "tone"), 2)}}), encoding="utf-8")
    both = ["--rubric-scores", str(scores), "--template-rubric-scores", str(t_scores)]

    for argv in (
        ["--compare", str(llm), str(tpl)],  # no scores
        ["--compare", str(llm), str(tpl), "--rubric-scores", str(scores)],  # one score file
        ["--compare", str(dev_llm), str(tpl), *both],  # not the test split
        ["--compare", str(tpl), str(llm), *both],  # sources swapped
    ):
        with pytest.raises(SystemExit) as exc:
            coach_eval.main(argv)
        assert exc.value.code == 2, argv

    seen = {}
    monkeypatch.setattr(coach_eval, "write_gate_report",
                        lambda summary, *, rubric_delta: seen.setdefault("d", rubric_delta) or True)
    coach_eval.main(["--compare", str(llm), str(tpl), *both])
    assert seen["d"] == Decimal("1.00")


# --- 429 rate limiting (c12-eval-ratelimit) ---------------------------------------------


def test_eval_waits_on_rate_limit_and_does_not_count_it_as_attempt():
    from app.coach.eval import run_eval
    from app.coach.llm import FakeLlmClient, LlmRateLimited

    recorded = load(ROOT / "recorded" / "dev" / "01_sugar_swap.json")
    client = FakeLlmClient(responses=[
        LlmRateLimited("rate limited", retry_after_seconds=5),
        LlmRateLimited("rate limited", retry_after_seconds=None),
        recorded[0],
    ])
    sleeps: list[float] = []

    summary, run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "01_sugar_swap.json"], split="dev",
        client=client, eval_root=ROOT, sleep_fn=sleeps.append,
    )
    try:
        assert sleeps == [5, 20]  # explicit retry-after, then the 20s default
        (rec,) = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
        assert rec["status"] == "ok"
        assert rec["first_try_ok"] is True
        assert rec["fallback"] is False
        # Only one real call succeeded: the two 429s must not show up as attempts.
        assert summary["first_try_pass_rate"] == "1.00"
        assert summary["rate_limited"] == 0
    finally:
        run_path.unlink()


def test_eval_marks_rate_limited_after_max_waits_and_excludes_from_metrics():
    from app.coach.eval import RATE_LIMIT_MAX_WAITS, run_eval
    from app.coach.llm import FakeLlmClient, LlmRateLimited

    client = FakeLlmClient(
        responses=[LlmRateLimited("rate limited") for _ in range(RATE_LIMIT_MAX_WAITS)]
    )
    sleeps: list[float] = []

    summary, run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "01_sugar_swap.json"], split="dev",
        client=client, eval_root=ROOT, sleep_fn=sleeps.append,
    )
    try:
        assert len(sleeps) == RATE_LIMIT_MAX_WAITS
        assert all(s == 20.0 for s in sleeps)
        (rec,) = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
        assert rec["status"] == "rate_limited"
        # Excluded from every metric: no first_try_ok, no hard_failure, no cost/latency.
        assert summary["n"] == 1
        assert summary["rate_limited"] == 1
        assert summary["hard_failures"] == 0
        assert summary["first_try_pass_rate"] == "0"
    finally:
        run_path.unlink()


def test_eval_content_filtered_excluded_from_first_try():
    from app.coach.eval import run_eval
    from app.coach.llm import FakeLlmClient, LlmContentFiltered

    client = FakeLlmClient(
        responses=[LlmContentFiltered("blocked", categories=frozenset({"jailbreak"}))]
    )
    summary, run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "01_sugar_swap.json"], split="dev",
        client=client, eval_root=ROOT,
    )
    try:
        (rec,) = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
        assert rec["status"] == "content_filtered"
        assert rec["categories"] == ["jailbreak"]
        assert rec["output"]["suggestions"] == []  # no static fallback
        assert rec["hard_failure"] is False
        # Excluded from every metric that assumes a completed call.
        assert "first_try_ok" not in rec or rec["status"] != "ok"
        assert summary["n"] == 1
        assert summary["content_filtered"] == 1
        assert summary["hard_failures"] == 0
        assert summary["first_try_pass_rate"] == "0"
        assert summary["latency_p50_ms"] == 0
        assert summary["tokens_in"] == 0 and summary["tokens_out"] == 0
    finally:
        run_path.unlink()


def test_eval_records_attempt_errors():
    from app.coach.eval import load_from_dir, run_eval
    from app.coach.llm import FakeLlmClient

    texts = load_from_dir(RUNNER_REPLAY, "dev", "03_partial_salt")  # invalid, then valid
    client = FakeLlmClient(responses=list(texts))
    summary, run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "03_partial_salt.json"], split="dev",
        client=client, eval_root=ROOT,
    )
    try:
        (rec,) = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
        assert rec["first_try_ok"] is False
        assert len(rec["attempt_errors"]) == 2
        assert rec["attempt_errors"][0]  # the first (failing) attempt has errors recorded
        assert all({"code", "detail"} == set(e) for e in rec["attempt_errors"][0])
        assert rec["attempt_errors"][1] is None  # the second attempt passed validation
    finally:
        run_path.unlink()
    del summary


def test_record_does_not_overwrite_committed_fixtures(tmp_path):
    from app.coach.eval import run_eval
    from app.coach.llm import FakeLlmClient

    committed = ROOT / "recorded" / "dev" / "01_sugar_swap.json"
    before = committed.read_bytes()
    client = FakeLlmClient(
        responses=["not touching the committed fixture", "still not touching it"]
    )
    summary, _run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "01_sugar_swap.json"], split="dev",
        client=client, eval_root=ROOT, record=True, run_path=tmp_path / "run.jsonl",
        now="20261013T000000Z",
    )
    del summary
    try:
        assert committed.read_bytes() == before
        recorded_run_dir = ROOT / "recorded_runs" / "20261013T000000Z-dev"
        assert (recorded_run_dir / "dev" / "01_sugar_swap.json").exists()
    finally:
        import shutil

        shutil.rmtree(ROOT / "recorded_runs", ignore_errors=True)


def test_latency_percentiles_exclude_records_without_calls():
    from app.coach.eval import _summarize

    records = [
        {"sheet": "dev/a.json", "case": "a", "source": "llm", "status": "ok",
         "fallback": False, "first_try_ok": True, "hard_failure": False,
         "tokens_in": 10, "tokens_out": 5, "cost_eur": "0.01", "latency_ms": 500,
         "called_llm": True, "output": {}},
        # Never called the model (e.g. a cached/template-only record): must not drag
        # the percentiles down to 0.
        {"sheet": "dev/b.json", "case": "b", "source": "llm", "status": "ok",
         "fallback": False, "first_try_ok": True, "hard_failure": False,
         "tokens_in": 0, "tokens_out": 0, "cost_eur": "0", "latency_ms": 0,
         "called_llm": False, "output": {}},
    ]
    summary = _summarize(records, "llm", ROOT)
    assert summary["latency_p50_ms"] == 500
    assert summary["latency_p95_ms"] == 500


def test_record_writes_content_filter_sentinel(tmp_path):
    import shutil

    from app.coach.eval import run_eval
    from app.coach.llm import FakeLlmClient, LlmContentFiltered

    client = FakeLlmClient(responses=[LlmContentFiltered("blocked", frozenset({"jailbreak"}))])
    _summary, _run_path = run_eval(
        source="llm", sheets=[ROOT / "dev" / "10_injection_name.json"], split="dev",
        client=client, eval_root=ROOT, record=True, run_path=tmp_path / "run.jsonl",
        now="20261013T000001Z", pace_seconds=0,
    )
    run_dir = ROOT / "recorded_runs" / "20261013T000001Z-dev"
    try:
        saved = run_dir / "dev" / "10_injection_name.json"
        assert json.loads(saved.read_text(encoding="utf-8")) == [{"content_filter": ["jailbreak"]}]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _replay_split(split):
    from app.coach.eval import run_eval

    summary, run_path = run_eval(source="llm", split=split, replay=True, eval_root=ROOT)
    run_path.unlink()
    return summary


def test_replay_dev_reproduces_gate():
    # Recorded live in run 20261014T145026Z-dev (v4 prompt), which scored first-try 0.90.
    summary = _replay_split("dev")
    assert summary["hard_failures"] == 0
    assert str(summary["first_try_pass_rate"]) == "0.90"
    assert summary["content_filtered"] == 2


def test_replay_test_reproduces_gate():
    summary = _replay_split("test")
    assert summary["hard_failures"] == 0
    assert str(summary["first_try_pass_rate"]) == "1.00"
    assert summary["content_filtered"] == 1


def test_no_empty_recordings():
    files = list((ROOT / "recorded").rglob("*.json"))
    assert files
    assert [f.name for f in files if load(f) == []] == []


# --- mixed weeks, ordering and usefulness (prompt v5) -----------------------------------


def test_mixed_weeks_have_several_contributors_and_competing_problems():
    names = {}
    for split in ("dev_mixed", "test_mixed"):
        sheets = {p.stem[3:]: load(p) for p in sorted((ROOT / split).glob("*.json"))}
        assert len(sheets) == 6
        names[split] = {c["name"] for s in sheets.values() for f in s["facts"]
                        for c in f["contributors"]}

        def fact(case, rule, sheets=sheets):
            return next(f for f in sheets[case]["facts"] if f["rule"] == rule)

        sugar = fact("mixed_sugar", "sugars_daily")
        assert len({c["share"][0] for c in sugar["contributors"]}) == 3  # no 1.00 shares
        assert sugar["swaps"][0]["for"] != sugar["contributors"][0]["id"]  # not for the top one
        assert fact("energy_and_salt", "energy_vs_target")["claim"] == "above"
        assert fact("energy_and_salt", "salt_daily")["values"]["days_exceeding"] == "4"
        assert fact("competing_limits", "sugars_daily")["values"]["days_exceeding"] == "2"
        assert fact("competing_limits", "salt_daily")["values"]["days_exceeding"] == "7"
        assert len(fact("weekend_sugar", "sugars_daily")["days"]) == 2
        assert fact("low_protein_low_energy", "energy_vs_target")["claim"] == "below"
        assert fact("low_protein_low_energy", "protein_vs_target")["claim"] == "below"
        salt = fact("undeclared_among_others", "salt_daily")
        assert len(salt["additions_undeclared"]) == 1 and len(salt["contributors"]) == 3
    assert not names["dev_mixed"] & names["test_mixed"]


def test_prioritise_orders_by_goal_then_days_and_limits_keep_going():
    from app.coach.service import prioritise

    def suggestion(ids, kind, rule):
        return {"kind": kind, "title": "t", "body": "b", "fact_ids": [ids[rule]], "swap_ids": []}

    sheet = load(ROOT / "dev_mixed" / "03_competing_limits.json")
    ids = {f["rule"]: f["id"] for f in sheet["facts"]}
    out = prioritise([suggestion(ids, "keep_going", "energy_vs_target"),
                      suggestion(ids, "habit", "sugars_daily"),
                      suggestion(ids, "habit", "salt_daily")], sheet)
    # Salt exceeds on 7 days, sugars on 2; two problems leave no room for keep_going.
    assert [x["fact_ids"][0] for x in out] == [ids["salt_daily"], ids["sugars_daily"]]

    sheet = load(ROOT / "dev_mixed" / "02_energy_and_salt.json")
    ids = {f["rule"]: f["id"] for f in sheet["facts"]}
    out = prioritise([suggestion(ids, "keep_going", "sugars_daily"),
                      suggestion(ids, "habit", "salt_daily"),
                      suggestion(ids, "habit", "energy_vs_target")], sheet)
    assert [x["fact_ids"][0] for x in out] == [ids["energy_vs_target"], ids["salt_daily"]]
    one = [suggestion(ids, "keep_going", "sugars_daily"), suggestion(ids, "habit", "salt_daily")]
    assert [x["kind"] for x in prioritise(one, sheet)] == ["habit", "keep_going"]


def test_usefulness_counts_problem_first_and_named_contributor():
    from app.coach.eval import usefulness

    sheet = load(ROOT / "dev_mixed" / "01_mixed_sugar.json")
    sugar = next(f for f in sheet["facts"] if f["rule"] == "sugars_daily")
    calm = next(f for f in sheet["facts"] if f["claim"] == "within")
    top = sugar["contributors"][0]["name"]

    def rec(suggestions):
        return {"sheet": "dev_mixed/01_mixed_sugar.json", "status": "ok",
                "output": {"suggestions": suggestions}}

    good = rec([{"kind": "habit", "title": f"Have {top} on fewer days", "body": "b",
                 "fact_ids": [sugar["id"]]}])
    bad = rec([{"kind": "keep_going", "title": "Keep it up", "body": "b",
                "fact_ids": [calm["id"]]},
               {"kind": "habit", "title": "Cut back", "body": "b", "fact_ids": [sugar["id"]]}])
    assert usefulness([good], ROOT) == {
        "suggestions": 1, "keep_going_share": "0.00", "problem_first_rate": "1.00",
        "top_contributor_named_rate": "1.00", "sourced_advice_rate": "0.00"}
    assert usefulness([bad], ROOT) == {
        "suggestions": 2, "keep_going_share": "0.50", "problem_first_rate": "0.00",
        "top_contributor_named_rate": "0.00", "sourced_advice_rate": "0.00"}


def test_swap_basis_number_is_grounded():
    sheet = load(ROOT / "dev_mixed" / "04_weekend_sugar.json")
    sugar = next(f for f in sheet["facts"] if f["rule"] == "sugars_daily")
    swap = sugar["swaps"][0]

    def out(body):
        return {"suggestions": [{"kind": "swap", "title": "Try it", "body": body,
                                 "fact_ids": [sugar["id"]], "swap_ids": [swap["id"]]}]}

    assert validate_output(out(f"{swap['name']} lists 0 g per 100 ml."), sheet, [])[0]
    assert validate_output(out(f"{swap['name']} lists 0 g per 250 ml."), sheet, [])[0] is None


def test_wording_single_average_and_plural_days():
    from app.domain.coach_templates import template_suggestions

    sheet = load(ROOT / "dev" / "09_several_facts.json")
    assert "2400-2400" not in json.dumps(sheet) and "averaged 2400 kcal" in json.dumps(sheet)
    text = json.dumps(template_suggestions(sheet))
    assert "day(s)" not in text and "7 days" in text
    # Energy below target is in line with a goal to lose weight: the template leaves it alone.
    low = load(ROOT / "dev_mixed" / "05_low_protein_low_energy.json")
    titles = [s["title"] for s in template_suggestions(low)["suggestions"]]
    assert titles == ["Add more protein"]


def test_guideline_passages_are_checked():
    from app.coach.knowledge import attach_knowledge, queries, search

    sheet = load(ROOT / "dev_mixed" / "06_undeclared_among_others.json")
    view = attach_knowledge(sheet)  # no embedder: keyword ranking, as in an offline replay
    salt = next(f for f in view["facts"] if f["rule"] == "salt_daily")
    passages = [k for k in view["knowledge"] if k["for"] == salt["id"]]
    assert passages and list(queries(view)) == [salt["id"]]
    assert all(c["coach_safe"] for c in search("reduce salt", 5, safe_only=True))
    k = {"id": "K1", "for": salt["id"],
         "text": "use herbs and spices to flavour food, rather than salt"}
    view = {**view, "knowledge": [k]}

    def out(body, source_ids, fact_ids=(salt["id"],)):
        return {"suggestions": [{"kind": "habit", "title": "Season with herbs", "body": body,
                                 "fact_ids": list(fact_ids), "swap_ids": [],
                                 "source_ids": source_ids}]}

    good = "Flavour homemade meals with herbs and spices and leave the salt out."
    assert validate_output(out(good, [k["id"]]), view, [])[0]
    assert validate_output(out(good, ["K99"]), view, [])[0] is None  # passage not shown
    calm = next(f["id"] for f in view["facts"] if f["claim"] == "around")
    assert validate_output(out(good, [k["id"]], [calm]), view, [])[0] is None  # other fact
    # A listed passage the body does not draw on is dropped, not rejected.
    from app.coach.service import prioritise

    unrelated = out("Go for a walk after dinner on most days.", [k["id"]])
    assert validate_output(unrelated, view, [])[0]
    assert prioritise(unrelated["suggestions"], view)[0]["source_ids"] == []
    assert prioritise(out(good, [k["id"]])["suggestions"], view)[0]["source_ids"] == [k["id"]]
    # A37: an unverified card gets no guideline material at all.
    unverified = load(ROOT / "dev" / "13_unverified_card.json")
    assert attach_knowledge(unverified)["knowledge"] == []


def test_keyword_retrieval_finds_labelled_passages():
    from app.coach.knowledge import evaluate

    labels = load(ROOT.parent / "knowledge" / "labels.json")
    assert evaluate(labels, None)["bm25"]["recall@3"] >= 0.75


def test_undeclared_additions_need_a_fact_that_lists_them():
    def out(sheet, rule):
        fact = next(f for f in sheet["facts"] if f["rule"] == rule)
        return {"suggestions": [{
            "kind": "habit", "title": "Declare the additions",
            "body": "It was logged without its cooking salt: declare the additions next time.",
            "fact_ids": [fact["id"]], "swap_ids": []}]}

    plain = load(ROOT / "dev" / "03_partial_salt.json")
    assert validate_output(out(plain, "salt_daily"), plain, [])[0] is None
    dish = load(ROOT / "dev" / "25_undeclared_dish.json")
    assert validate_output(out(dish, "salt_daily"), dish, [])[0]


def test_rules_found_on_the_held_out_weeks():
    def one(sheet, body, rules, kind="habit", title="Next week"):
        ids = [f["id"] for f in sheet["facts"] if f["rule"] in rules]
        return validate_output({"suggestions": [{
            "kind": kind, "title": title, "body": body, "fact_ids": ids, "swap_ids": []}]},
            sheet, [])

    # Energy below target with a goal to lose weight is not something to fix.
    low = load(ROOT / "dev_mixed" / "05_low_protein_low_energy.json")
    parsed, errors = one(low, "Add a portion on more days to support your energy needs.",
                         {"energy_vs_target"})
    assert parsed is None and {e.code for e in errors} == {"direction"}
    assert one(low, "Add a portion of the largest protein source on more days.",
               {"protein_vs_target"})[0]

    # A nutrient with no cited fact about it may not be discussed.
    salty = load(ROOT / "dev_mixed" / "02_energy_and_salt.json")
    parsed, errors = one(salty, "Sugars stayed on track and protein was around your target.",
                         {"sugars_daily"}, kind="keep_going")
    assert parsed is None and "protein" in errors[0].detail
    assert one(salty, "Sugars stayed on track all week.", {"sugars_daily"}, kind="keep_going")[0]
    # A passing mention of a nutrient the week does have a fact about is fine.
    assert one(salty, "Keep sugars steady while working on energy.", {"sugars_daily"},
               kind="keep_going")[0]

    # A swap on offer for the named food must be used.
    weekend = load(ROOT / "dev_mixed" / "04_weekend_sugar.json")
    sugar = next(f for f in weekend["facts"] if f["rule"] == "sugars_daily")
    swap = sugar["swaps"][0]
    food = next(c["name"] for c in sugar["contributors"] if c["id"] == swap["for"])
    parsed, errors = one(weekend, f"Have {food} on fewer days.", {"sugars_daily"})
    assert parsed is None and "has a swap" in errors[0].detail
    other = next(c["name"] for c in sugar["contributors"] if c["id"] != swap["for"])
    assert one(weekend, f"Have {other} on fewer days.", {"sugars_daily"})[0]
