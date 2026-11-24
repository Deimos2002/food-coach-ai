"""Public coach playground: pick a sample week, get a live, checked review.

No database and no login: the weeks are the synthetic dev fact sheets from ``evals/coach``
and the review runs the same retrieval, prompt, two-attempt retry, validator and ordering
as the product (``app.coach.eval.run_retrying``). Live calls are capped per day; past the
cap, or with no model configured, the last stored review for that week is returned and
labelled as such.

    uvicorn app.demo:create_demo_app --factory
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.coach.eval import EVAL_ROOT, load_decoys, run_retrying, sheet_catalogue
from app.coach.knowledge import attach_knowledge, query_embedder, with_sources
from app.coach.llm import LlmClient, cost_eur
from app.coach.service import PROMPT_VERSION, UNAVAILABLE, load_prompt
from app.config import Settings

PAGE = Path(__file__).resolve().parent / "demo_static" / "index.html"
CACHE_PATH = EVAL_ROOT / "demo_cache.json"
DAILY_CAP_ENV = "COACH_DEMO_DAILY_REVIEWS"
# shortcut: flags are a JSONL file (on an Azure Files share in the deployed playground) read
# whole at startup. Move to a table if reviewers start relying on them.
FLAGS_PATH_ENV = "COACH_DEMO_FLAGS_PATH"
MAX_FLAGS = 1000
FLAG_REASONS = ("wrong_fact", "wrong_food", "bad_advice", "unsupported_source", "other")


class FlagIn(BaseModel):
    week: str
    card: int = Field(ge=0, le=10)
    reason: Literal[FLAG_REASONS]  # type: ignore[valid-type]
    note: str = Field(default="", max_length=280)
    generated_at: str  # which review the card belongs to: a newer live review may have replaced it
# id: (file under evals/coach, title, what the week is designed to show)
WEEKS = {
    "mixed-sugar": ("dev_mixed/01_mixed_sugar.json", "Sugar from several foods",
                    "The largest sugar source has no swap; the second one does."),
    "energy-salt": ("dev_mixed/02_energy_and_salt.json", "Energy up, salt up",
                    "Goal is to lose weight, energy is above target and one food drives both."),
    "competing": ("dev_mixed/03_competing_limits.json", "Two limits compete",
                  "Salt is exceeded every day, sugars on 2 days: salt should come first."),
    "weekend": ("dev_mixed/04_weekend_sugar.json", "A weekend-only problem",
                "Sugars are exceeded on Saturday and Sunday; a swap is on offer."),
    "low-protein": ("dev_mixed/05_low_protein_low_energy.json", "Low protein, low energy",
                    "Energy is already below target, so advice to eat less would be wrong."),
    "undeclared": ("dev_mixed/06_undeclared_among_others.json", "Undeclared cooking salt",
                   "A homemade dish was logged without its cooking additions."),
    "good-week": ("dev/08_good_week.json", "A good week",
                  "Nothing to fix: one encouragement, not three."),
    "injection": ("dev/10_injection_name.json", "Prompt injection in a product name",
                  "A food is named like an instruction. The request is blocked, not obeyed."),
}


def _sheet(week_id: str) -> dict[str, Any]:
    if week_id not in WEEKS:
        raise HTTPException(status_code=404, detail="unknown sample week")
    return json.loads((EVAL_ROOT / WEEKS[week_id][0]).read_text(encoding="utf-8"))


def create_demo_app(client: LlmClient | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if client is None and settings.azure_openai_endpoint and settings.azure_openai_api_key:
        from app.coach.llm import AzureLlmClient

        client = AzureLlmClient(settings)
    embed = query_embedder(settings)
    decoys = load_decoys()
    cache: dict[str, dict] = (
        json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    )
    used = {"day": "", "count": 0}
    lock = threading.Lock()
    flags: list[dict] = []
    if (path := os.environ.get(FLAGS_PATH_ENV)) and Path(path).exists():
        flags += [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
                  if line.strip()][-MAX_FLAGS:]

    def live_left() -> int:
        cap = int(os.environ.get(DAILY_CAP_ENV, "30"))
        today = datetime.now(UTC).date().isoformat()
        if used["day"] != today:
            used.update(day=today, count=0)
        return max(cap - used["count"], 0) if client is not None else 0

    app = FastAPI(title="Food Coach playground", docs_url=None, redoc_url=None)

    @app.get("/")
    def page() -> FileResponse:
        # Revalidate on every visit, or browsers keep showing the page from before a deploy.
        return FileResponse(PAGE, headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/weeks")
    def weeks() -> dict[str, Any]:
        with lock:
            left = live_left()
        return {"live_left": left, "prompt_version": PROMPT_VERSION,
                "weeks": [{"id": k, "title": title, "about": about}
                          for k, (_, title, about) in WEEKS.items()]}

    @app.get("/api/weeks/{week_id}")
    def week(week_id: str) -> dict[str, Any]:
        return _sheet(week_id)

    @app.get("/api/weeks/{week_id}/review")
    def stored(week_id: str) -> dict[str, Any]:
        _sheet(week_id)
        if week_id not in cache:
            raise HTTPException(status_code=404, detail="no stored review for this week")
        return {**cache[week_id], "mode": "cached"}

    @app.post("/api/flags", status_code=201)
    def flag(body: FlagIn) -> dict[str, Any]:
        _sheet(body.week)
        shown = cache.get(body.week, {})
        if shown.get("generated_at") != body.generated_at:
            raise HTTPException(status_code=409, detail="That review has been replaced by a newer one.")
        cards = shown.get("suggestions", [])
        if body.card >= len(cards):
            raise HTTPException(status_code=404, detail="no such card in the review on show")
        card = cards[body.card]
        # The card is copied from the server's review, so a flag cannot carry made-up advice.
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "week": body.week,
                 "reason": body.reason, "note": body.note.strip(), "title": card["title"],
                 "body": card["body"], "generated_at": body.generated_at}
        with lock:
            if len(flags) >= MAX_FLAGS:
                raise HTTPException(status_code=429, detail="The flag list is full.")
            flags.append(entry)
            if path := os.environ.get(FLAGS_PATH_ENV):
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    @app.get("/api/flags")
    def list_flags() -> list[dict[str, Any]]:
        return flags[::-1][:50]

    @app.post("/api/weeks/{week_id}/review")
    def review(week_id: str) -> dict[str, Any]:
        sheet = _sheet(week_id)
        with lock:
            allowed = live_left() > 0
            if allowed:
                used["count"] += 1  # counted before the call: a failed call still costs
        if not allowed or client is None:
            if week_id in cache:
                return {**cache[week_id], "mode": "cached"}
            raise HTTPException(status_code=429, detail="Today's live reviews are used up.")
        view = attach_knowledge(sheet, embed)
        suggestions, attempts, rate_limited = run_retrying(
            client, load_prompt(), view, sheet_catalogue(sheet, decoys),
        )
        filtered = bool(attempts) and attempts[-1].error == "LlmContentFiltered"
        result = {
            "mode": "live",
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "prompt_version": PROMPT_VERSION,
            "suggestions": with_sources(suggestions, view) if suggestions else [],
            "message": None if suggestions else (
                "Azure's content filter blocked this request, so no review was produced."
                if filtered else "The model is busy. Try again in a minute." if rate_limited
                else UNAVAILABLE["validation" if attempts and attempts[-1].ok is False
                                 else "llm_error"]),
            "attempts": [{"attempt": a.attempt, "passed": a.ok, "errors": a.errors or [],
                          "blocked": a.error == "LlmContentFiltered"}
                         for a in attempts if a.text is not None or a.error],
            # Second model calls that checked each cited passage against the advice.
            "citations_checked": sum(a.text is None and not a.error for a in attempts),
            "passages": [{k: p[k] for k in ("id", "for", "source", "locator", "text")}
                         for p in view["knowledge"]],
            "latency_ms": sum(a.result.latency_ms for a in attempts if a.result),
            "cost_eur": str(sum((cost_eur(a.result, settings) for a in attempts if a.result),
                                start=0)),
        }
        if not rate_limited:
            with lock:
                cache[week_id] = result
        return result

    return app
