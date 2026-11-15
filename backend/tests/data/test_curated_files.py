import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
import yaml

D = Path(__file__).resolve().parents[2] / "catalogue_data"
FF_HEADER = [
    "chain", "item", "grams", "kcal", "protein_g", "carbs_g", "sugars_g",
    "fat_g", "sat_fat_g", "fibre_g", "salt_g", "source_url", "checked_on",
]
CHAINS = {
    "McDonald's", "Burger King", "KFC", "Quick", "O'Tacos", "Subway",
    "Five Guys", "Chamas Tacos", "G La Dalle", "Tasty Crousty",
}


def _codes():
    ws = openpyxl.load_workbook(D / "ciqual_2025_en.xlsx", read_only=True).worksheets[0]
    return {str(r[6]).strip() for i, r in enumerate(ws.iter_rows(values_only=True)) if i}


def _csv(name):
    with open(D / name, encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def _pos(v):
    try:
        return Decimal(v) > 0
    except (InvalidOperation, TypeError):
        return False


def _dishes():
    return yaml.safe_load((D / "dishes.yaml").read_text(encoding="utf-8"))


def test_files_exist():
    for n in ["ciqual_2025_en.xlsx", "portions.csv", "fastfood.csv", "dishes.yaml", "SOURCES.md"]:
        assert (D / n).is_file(), n


def test_portions():
    head, rows = _csv("portions.csv")
    assert head == ["alim_code", "label_en", "grams", "source"]
    assert len(rows) >= 150
    codes = _codes()
    for r in rows:
        assert r["alim_code"] in codes, r
        assert _pos(r["grams"]), r
        assert r["label_en"].strip() and r["source"].startswith("https://"), r
    keys = [(r["alim_code"], r["label_en"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_fastfood():
    head, rows = _csv("fastfood.csv")
    assert head == FF_HEADER
    today = dt.date.today()
    for r in rows:
        assert r["chain"].strip() and r["item"].strip(), r
        assert r["source_url"].startswith("https://"), r
        assert dt.date.fromisoformat(r["checked_on"]) <= today, r
        if r["grams"].strip():  # blank grams = published no weight, importer skips
            assert _pos(r["grams"]), r
        for c in FF_HEADER[3:11]:
            if r[c].strip():
                assert Decimal(r[c]) >= 0, r
    keys = [(r["chain"], r["item"]) for r in rows]
    assert len(keys) == len(set(keys))


def test_dishes():
    dishes = _dishes()
    codes = _codes()
    for d in dishes:
        assert d["name"] and d["name_en"] and d["cuisine"], d
        assert d["source_url"].startswith("https://"), d
        assert d["ingredients"], d
        for i in d["ingredients"]:
            assert str(i["alim_code"]) in codes, (d["name"], i)
            assert _pos(str(i["grams"])), (d["name"], i)
        if d.get("estimate"):
            assert d.get("chain"), d
    assert len([d for d in dishes if not d.get("chain")]) >= 40


def test_chains_and_no_overlap():
    _, rows = _csv("fastfood.csv")
    dishes = _dishes()
    covered = {r["chain"] for r in rows} | {d["chain"] for d in dishes if d.get("chain")}
    assert CHAINS <= covered, CHAINS - covered
    ff = {(r["chain"], r["item"]) for r in rows}
    dd = {(d["chain"], d["name"]) for d in dishes if d.get("chain")}
    dd |= {(d["chain"], d["name_en"]) for d in dishes if d.get("chain")}
    assert not ff & dd, ff & dd


def test_additional_halal_chains_in_lille_or_toulouse_are_estimated():
    dishes = _dishes()
    added = [
        d for d in dishes
        if d.get("chain") in {"Chick'n Beef", "Chicken Street", "Black & White Burger"}
    ]
    assert {(d["chain"], d["name_en"]) for d in added} == {
        ("Chick'n Beef", "Classic Gourmet burger"),
        ("Black & White Burger", "Black & Marquise Baguette"),
        ("Chicken Street", "Street Deal - Little Tenders"),
    }
    assert all(
        d.get("estimate") is True
        and d["source_url"].startswith("https://")
        and d["checked_on"] == "2026-10-15"
        for d in added
    )
