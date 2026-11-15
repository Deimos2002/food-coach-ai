"""Build the coach's guideline corpus from the public WHO documents.

    python -m scripts.build_knowledge DOWNLOAD_DIR [--embed]

DOWNLOAD_DIR holds the files named in SOURCES (fetch each from its ``url``; the two PDFs
are the English bitstreams of the IRIS records). The documents themselves are not
committed: only the chunks, with their source and locator, in ``knowledge/guidelines/``.
``--embed`` also writes ``embeddings.json`` using the Azure embedding deployment
(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT).

Needs ``pypdf`` and ``beautifulsoup4`` (dev extras); the app itself needs neither.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.coach.knowledge import CORPUS_DIR, EMBED_DIMENSIONS, azure_embed
from app.domain.coach_validator import FORBIDDEN_PATTERNS

# file: (doc id, title, url, PDF pages to keep as 1-based inclusive ranges, or None for HTML)
SOURCES = {
    "who_sugars_2015.pdf": (
        "who-sugars-2015", "WHO guideline: sugars intake for adults and children (2015)",
        "https://iris.who.int/handle/10665/149782", [(9, 28)]),
    "who_sodium_2012.pdf": (
        "who-sodium-2012", "WHO guideline: sodium intake for adults and children (2012)",
        "https://iris.who.int/handle/10665/77985", [(9, 29), (34, 35)]),
    "who_healthy_diet.html": (
        "who-healthy-diet", "WHO fact sheet: healthy diet",
        "https://www.who.int/news-room/fact-sheets/detail/healthy-diet", None),
    "who_sodium_reduction.html": (
        "who-sodium-reduction", "WHO fact sheet: sodium reduction",
        "https://www.who.int/news-room/fact-sheets/detail/sodium-reduction", None),
    "who_emro_limit_fat_salt_sugar.html": (
        "who-emro-limit", "WHO EMRO: limit fat, salt and sugar intake",
        "https://www.emro.who.int/nutrition/reduce-fat-salt-and-sugar-intake/index.html", None),
    "who_healthyathome_diet.html": (
        "who-healthyathome", "WHO #HealthyAtHome: healthy diet",
        ("https://www.who.int/campaigns/connecting-the-world-to-combat-coronavirus/"
         "healthyathome/healthyathome---healthy-diet"), None),
    "who_behealthy_diet.html": (
        "who-behealthy", "WHO Be Healthy: healthy diet",
        "https://www.who.int/initiatives/behealthy/healthy-diet", None),
}
CHUNK_WORDS = 110
# The coach never uses medical wording, so it is only ever shown passages without any.
_MEDICAL = re.compile(
    "|".join(FORBIDDEN_PATTERNS)
    + r"|blood pressure|hypertens|cardiovasc|stroke|heart|caries|dental|mortality|death"
    r"|\bncds?\b|noncommunicable|\brisk|illness|patient|morbidity|kidney|renal|weight gain",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.;:!?])\s+")


def _pack(text: str) -> list[str]:
    """Consecutive whole sentences packed into chunks of about CHUNK_WORDS words. A chunk
    never mixes sentences with and without medical wording, so practical advice stays
    usable when the sentence next to it explains a health risk."""
    chunks: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCE.split(" ".join(text.split())):
        full = sum(len(s.split()) for s in current) + len(sentence.split()) > CHUNK_WORDS
        flips = bool(_MEDICAL.search(sentence)) != bool(_MEDICAL.search(" ".join(current)))
        if current and (full or flips):
            chunks.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c.split()) >= 6]


def _pdf_sections(path: Path, ranges: list[tuple[int, int]]) -> list[tuple[str, str]]:
    from pypdf import PdfReader

    pages = PdfReader(str(path)).pages
    out = []
    for first, last in ranges:
        for n in range(first, last + 1):
            lines = [ln.strip() for ln in pages[n - 1].extract_text().split("\n") if ln.strip()]
            # Drop the running header and the printed page number.
            body = [ln for ln in lines[1:] if not re.fullmatch(r"[\divxIVX]+", ln)]
            out.append((f"p. {n}", " ".join(body)))
    return out


def _html_sections(path: Path) -> list[tuple[str, str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    main = (soup.find(attrs={"itemprop": "articleBody"}) or soup.find("article")
            or soup.find("main") or soup)
    for chrome in main(["nav", "header", "footer", "script", "style", "form"]):
        chrome.decompose()
    out: list[tuple[str, str]] = []
    heading, parts = "Overview", []
    for tag in main.find_all(["h2", "h3", "p", "li"]):
        text = tag.get_text(" ", strip=True)
        links = " ".join(a.get_text(" ", strip=True) for a in tag.find_all("a"))
        if tag.name == "li" and (tag.find(["ul", "ol"]) or len(links) > len(text) / 2):
            continue  # menus and link lists, not content
        if tag.name in ("h2", "h3"):
            if parts:
                out.append((heading, " ".join(parts)))
            heading, parts = text, []
        elif text:
            parts.append(text if text[-1] in ".;:!?" else text + ";")
    if parts:
        out.append((heading, " ".join(parts)))
    return out


def build(download_dir: Path) -> list[dict]:
    chunks = []
    for name, (doc, title, url, ranges) in SOURCES.items():
        path = download_dir / name
        sections = _pdf_sections(path, ranges) if ranges else _html_sections(path)
        n = 0
        for locator, text in sections:
            for piece in _pack(text):
                n += 1
                chunks.append({
                    "id": f"{doc}#{n}", "doc": doc, "title": title, "url": url,
                    "locator": locator, "text": piece,
                    "coach_safe": not _MEDICAL.search(piece),
                })
    return chunks


def main(argv: list[str]) -> int:
    chunks = build(Path(argv[0]))
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    (CORPUS_DIR / "chunks.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8",
    )
    print(len(chunks), "chunks,", sum(c["coach_safe"] for c in chunks), "usable by the coach")
    if "--embed" in argv:
        vectors = azure_embed([f"{c['title']}. {c['locator']}. {c['text']}" for c in chunks])
        (CORPUS_DIR / "embeddings.json").write_text(
            json.dumps({c["id"]: [round(x, 5) for x in v]
                        for c, v in zip(chunks, vectors, strict=True)}),
            encoding="utf-8",
        )
        print("embedded at", EMBED_DIMENSIONS, "dimensions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
