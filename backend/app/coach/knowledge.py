"""Guideline retrieval for the weekly coach.

The corpus is a few hundred chunks of public WHO documents (``knowledge/guidelines/``,
built by ``scripts/build_knowledge.py``), small enough to rank in memory: BM25 over the
text, cosine over stored embeddings, fused by reciprocal rank. Without a query embedding
(no deployment configured, the call failed, or an offline replay) the ranking is BM25 only.

For each problem in a week the coach is shown the best passages as ``knowledge`` entries
(K1, K2, ...). It may only use passages without medical wording (``coach_safe``), and a
sugars or salt fact gets passages only when its guideline card is verified (A37).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.config import BACKEND_DIR, Settings
from app.services.search import normalize

CORPUS_DIR = BACKEND_DIR / "knowledge" / "guidelines"
EMBED_DIMENSIONS = 256
PASSAGES_PER_FACT = 2
_RRF_K = 60
_BM25_K1, _BM25_B = 1.5, 0.75
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "this", "to", "was", "were", "which", "with", "how", "can", "your",
    "you",
})
Embed = Callable[[str], list[float] | None]


def tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", normalize(text)):
        if t in _STOP:
            continue
        # shortcut: plural folding only ("sugars" = "sugar"); a stemmer if recall needs it.
        out.append(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t)
    return out


@lru_cache
def corpus() -> tuple[list[dict[str, Any]], dict[str, list[float]]]:
    path = CORPUS_DIR / "chunks.jsonl"
    if not path.exists():
        return [], {}
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    vectors_path = CORPUS_DIR / "embeddings.json"
    vectors = json.loads(vectors_path.read_text(encoding="utf-8")) if vectors_path.exists() else {}
    return chunks, vectors


@lru_cache
def _bm25_index() -> tuple[list[Counter[str]], dict[str, float], float]:
    chunks, _ = corpus()
    docs = [Counter(tokens(f"{c['locator']} {c['text']}")) for c in chunks]
    df = Counter(t for d in docs for t in d)
    n = len(docs)
    idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
    return docs, idf, sum(sum(d.values()) for d in docs) / max(n, 1)


def _bm25(query: str) -> list[float]:
    docs, idf, avg_len = _bm25_index()
    terms = tokens(query)
    scores = []
    for d in docs:
        length = sum(d.values())
        scores.append(sum(
            idf.get(t, 0.0) * d[t] * (_BM25_K1 + 1)
            / (d[t] + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avg_len))
            for t in terms if t in d
        ))
    return scores


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def search(
    query: str, k: int, *, embed: Embed | None = None, mode: str = "hybrid",
    safe_only: bool = False,
) -> list[dict[str, Any]]:
    """Top ``k`` chunks. ``mode``: ``bm25``, ``dense`` or ``hybrid`` (reciprocal rank fusion)."""
    chunks, vectors = corpus()
    if not chunks:
        return []
    rankings: list[list[int]] = []
    if mode in ("bm25", "hybrid"):
        scores = _bm25(query)
        rankings.append(sorted((i for i, s in enumerate(scores) if s > 0),
                               key=lambda i: -scores[i]))
    query_vector = embed(query) if embed and vectors and mode in ("dense", "hybrid") else None
    if query_vector:
        sims = [_cosine(query_vector, vectors[c["id"]]) for c in chunks]
        rankings.append(sorted(range(len(chunks)), key=lambda i: -sims[i]))
    fused: Counter[int] = Counter()
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            fused[i] += 1 / (_RRF_K + rank + 1)
    ordered = sorted(fused, key=lambda i: (-fused[i], i))
    return [chunks[i] for i in ordered if chunks[i]["coach_safe"] or not safe_only][:k]


# --- queries for a week ----------------------------------------------------------------

_QUERY = {
    ("sugars_daily", "exceeds"): "how to reduce sugars: sugary drinks, snacks and sweets",
    ("salt_daily", "exceeds"): "how to reduce salt and sodium in food and cooking",
    ("energy_vs_target", "above"): "energy balance: fats, portion sizes and energy-dense foods",
    ("protein_vs_target", "below"): "protein foods: pulses, nuts, fish, eggs, dairy and meat",
}


def queries(view: dict[str, Any]) -> dict[str, str]:
    """Fact id -> search query for every problem the coach may get passages for."""
    goal = (view.get("goal") or {}).get("type")
    out = {}
    for fact in view.get("facts", []):
        base = _QUERY.get((fact.get("rule"), fact.get("claim")))
        if base is None:
            continue
        if fact["rule"] in ("sugars_daily", "salt_daily") and not fact.get("card_id"):
            continue  # A37: no guideline material for an unverified card
        if fact["rule"] == "energy_vs_target" and goal == "gain":
            continue
        foods = ", ".join(c["name"] for c in fact.get("contributors") or [])
        out[fact["id"]] = f"{base}. Foods: {foods}" if foods else base
    return out


def attach_knowledge(view: dict[str, Any], embed: Embed | None = None) -> dict[str, Any]:
    """A copy of the LLM view with a ``knowledge`` list: the passages the coach may cite."""
    knowledge: list[dict[str, Any]] = []
    for fact_id, query in queries(view).items():
        for chunk in search(query, PASSAGES_PER_FACT, embed=embed, safe_only=True):
            knowledge.append({
                "id": f"K{len(knowledge) + 1}", "for": fact_id, "chunk": chunk["id"],
                "source": chunk["title"], "locator": chunk["locator"], "text": chunk["text"],
            })
    return {**view, "knowledge": knowledge}


def with_sources(suggestions: list[dict], view: dict[str, Any]) -> list[dict]:
    """Resolve each suggestion's ``source_ids`` into ``sources`` the app can link to."""
    by_id = {c["id"]: c for c in corpus()[0]}
    cited = {k["id"]: by_id[k["chunk"]] for k in view.get("knowledge", []) if k["chunk"] in by_id}
    out = []
    for s in suggestions:
        sources = [{"title": cited[i]["title"], "url": cited[i]["url"],
                    "locator": cited[i]["locator"]}
                   for i in s.get("source_ids") or [] if i in cited]
        out.append({**s, "sources": sources})
    return out


# --- embeddings --------------------------------------------------------------------------


def azure_embed(texts: list[str], settings: Settings | None = None) -> list[list[float]]:
    import openai

    settings = settings or Settings()
    client = openai.AzureOpenAI(
        azure_endpoint=settings.azure_openai_endpoint, api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_api_version, timeout=30.0, max_retries=2,
    )
    out: list[list[float]] = []
    for start in range(0, len(texts), 64):
        response = client.embeddings.create(
            model=settings.azure_openai_embedding_deployment,
            input=texts[start:start + 64], dimensions=EMBED_DIMENSIONS,
        )
        out += [d.embedding for d in response.data]
    return out


def query_embedder(settings: Settings) -> Embed | None:
    """Embeds one query, or returns None so retrieval falls back to BM25: a failed
    embedding call must never cost the user their review."""
    if not settings.azure_openai_embedding_deployment:
        return None

    def embed(query: str) -> list[float] | None:
        try:
            return azure_embed([query], settings)[0]
        except Exception:  # noqa: BLE001 - any failure means "rank without embeddings"
            return None

    return embed


# --- retrieval quality -------------------------------------------------------------------


def evaluate(labels: list[dict[str, Any]], embed: Embed | None, k: int = 3) -> dict[str, dict]:
    """recall@k (any relevant passage in the top k) and MRR per ranking mode, over the
    passages the coach may use. Labels: ``evals/knowledge/labels.json``."""
    out = {}
    for mode in ("bm25", "dense", "hybrid"):
        hits, rr = 0, 0.0
        for row in labels:
            ids = [c["id"] for c in search(row["query"], 10, embed=embed, mode=mode,
                                           safe_only=True)]
            ranks = [i + 1 for i, cid in enumerate(ids) if cid in row["relevant"]]
            hits += bool(ranks and ranks[0] <= k)
            rr += 1 / ranks[0] if ranks else 0.0
        out[mode] = {f"recall@{k}": round(hits / len(labels), 2), "mrr": round(rr / len(labels), 2)}
    return out


if __name__ == "__main__":
    _labels = json.loads((BACKEND_DIR / "evals" / "knowledge" / "labels.json").read_text("utf-8"))
    print(json.dumps(evaluate(_labels, query_embedder(Settings())), indent=2))
