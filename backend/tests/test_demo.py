"""The public coach playground: a live checked review, a daily cap, and a stored fallback."""

import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app import demo
from app.coach.llm import FakeLlmClient, LlmContentFiltered
from app.config import Settings

SETTINGS = Settings(coach_price_in_per_1k=Decimal("0.001"), coach_price_out_per_1k=Decimal("0.002"))


def _valid(week_id="competing"):
    sheet = json.loads((demo.EVAL_ROOT / demo.WEEKS[week_id][0]).read_text(encoding="utf-8"))
    salt = next(f for f in sheet["facts"] if f["rule"] == "salt_daily")
    return json.dumps({"suggestions": [{
        "kind": "habit", "title": "Take a smaller portion",
        "body": f"Your recorded salt exceeds the guideline on {len(salt['days'])} days.",
        "fact_ids": [salt["id"]], "swap_ids": [], "source_ids": []}]})


def test_live_review_is_checked_then_capped_and_served_from_the_store(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "CACHE_PATH", tmp_path / "none.json")
    monkeypatch.setenv(demo.DAILY_CAP_ENV, "2")
    fake = FakeLlmClient(responses=["not json", _valid(), _valid()])
    client = TestClient(demo.create_demo_app(fake, SETTINGS))

    home = client.get("/")
    assert "Food Coach playground" in home.text and home.headers["cache-control"] == "no-cache"
    listing = client.get("/api/weeks").json()
    assert listing["live_left"] == 2 and len(listing["weeks"]) == len(demo.WEEKS)
    assert client.get("/api/weeks/competing").json()["facts"]
    assert client.get("/api/weeks/nope").status_code == 404

    first = client.post("/api/weeks/competing/review").json()
    assert first["mode"] == "live" and len(first["suggestions"]) == 1
    assert [a["passed"] for a in first["attempts"]] == [False, True]  # rejected, then fixed
    assert first["attempts"][0]["errors"] and first["passages"]
    assert client.post("/api/weeks/competing/review").json()["mode"] == "live"

    capped = client.post("/api/weeks/competing/review").json()
    assert capped["mode"] == "cached" and capped["suggestions"] == first["suggestions"]
    assert client.get("/api/weeks").json()["live_left"] == 0
    assert client.post("/api/weeks/good-week/review").status_code == 429  # nothing stored yet


def test_blocked_request_yields_no_review(monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "CACHE_PATH", tmp_path / "none.json")
    fake = FakeLlmClient(responses=[LlmContentFiltered("blocked", frozenset({"jailbreak"}))])
    body = TestClient(demo.create_demo_app(fake, SETTINGS)).post(
        "/api/weeks/injection/review").json()
    assert body["suggestions"] == [] and "content filter" in body["message"]
    assert body["attempts"][0]["blocked"] is True


def test_stored_review_is_served_without_a_model_call():
    client = TestClient(demo.create_demo_app(FakeLlmClient(responses=[]), SETTINGS))
    stored = client.get("/api/weeks/good-week/review").json()
    assert stored["mode"] == "cached" and stored["suggestions"] and stored["generated_at"]
    assert client.get("/api/weeks/nope/review").status_code == 404


def test_flag_copies_the_card_from_the_review_on_show(monkeypatch, tmp_path):
    flags_file = tmp_path / "flags.jsonl"
    monkeypatch.setenv(demo.FLAGS_PATH_ENV, str(flags_file))
    client = TestClient(demo.create_demo_app(FakeLlmClient(responses=[]), SETTINGS))
    shown = client.get("/api/weeks/competing/review").json()
    ok = {"week": "competing", "card": 1, "reason": "wrong_food", "note": " salt ",
          "generated_at": shown["generated_at"]}

    entry = client.post("/api/flags", json=ok).json()
    assert entry["title"] == shown["suggestions"][1]["title"] and entry["note"] == "salt"
    assert client.get("/api/flags").json()[0] == entry
    assert json.loads(flags_file.read_text(encoding="utf-8")) == entry
    # Survives a restart when the file is on a volume.
    restarted = TestClient(demo.create_demo_app(FakeLlmClient(responses=[]), SETTINGS))
    assert restarted.get("/api/flags").json() == [entry]

    assert client.post("/api/flags", json={**ok, "card": 9}).status_code == 404
    assert client.post("/api/flags", json={**ok, "generated_at": "older"}).status_code == 409
    assert client.post("/api/flags", json={**ok, "reason": "spam"}).status_code == 422
    assert client.post("/api/flags", json={**ok, "note": "x" * 281}).status_code == 422
    assert client.post("/api/flags", json={**ok, "week": "nope"}).status_code == 404


def test_intro_figures_match_the_committed_run_files():
    runs = demo.PAGE.parents[3] / "docs" / "evals" / "runs-2026-11-24"
    records = [json.loads(line) for split in ("test", "test_mixed", "test_random")
               for line in (runs / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
    sent = [r for r in records if r["status"] == "ok" and "first_try_ok" in r]
    reviewed = [r for r in sent if not r["hard_failure"]]
    first_try = sum(r["first_try_ok"] for r in sent)
    summary = json.loads((runs / "test.summary.json").read_text(encoding="utf-8"))
    page = demo.PAGE.read_text(encoding="utf-8")
    assert len(records) == 41 and len(sent) == len(reviewed) == 39 and first_try == 30
    assert summary["seeded_catch_rate"] == "1.00" and len(list(
        (demo.EVAL_ROOT / "seeded_bad").glob("*.json"))) == 20
    assert "over 41 held-out weeks: all <b>39</b>" in page and "<b>30</b> on the first try" in page
    assert "<b>20 of 20</b>" in page


def test_each_review_and_flag_writes_one_log_line_without_visitor_text(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(demo, "CACHE_PATH", tmp_path / "none.json")
    monkeypatch.setenv(demo.DAILY_CAP_ENV, "1")
    monkeypatch.delenv(demo.FLAGS_PATH_ENV, raising=False)
    client = TestClient(demo.create_demo_app(FakeLlmClient(responses=["not json", _valid()]), SETTINGS))
    caplog.set_level("INFO", logger="app.demo")

    live = client.post("/api/weeks/competing/review").json()
    client.post("/api/weeks/competing/review")  # over the cap: the stored review
    client.post("/api/flags", json={"week": "competing", "card": 0, "reason": "wrong_fact",
                                    "note": "my private words", "generated_at": live["generated_at"]})

    lines = [r.getMessage() for r in caplog.records if r.name == "app.demo"]
    assert lines[0].startswith("review week=competing mode=live outcome=reviewed attempts=fail,pass errors=schema")
    assert "cards=1" in lines[0] and "cost_eur=" in lines[0]
    assert lines[1] == "review week=competing mode=cached reason=daily_cap stored=True"
    assert lines[2] == "flag week=competing card=0 reason=wrong_fact note_len=16 total=1"
    assert not any("private" in line for line in lines)
