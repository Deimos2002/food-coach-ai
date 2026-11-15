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

    assert "Food Coach playground" in client.get("/").text
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
