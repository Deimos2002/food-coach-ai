"""Pure parse and validation of the curated data folder (no database)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest
import yaml

from app.domain.values import Nutrient
from app.importers.curated import CuratedError, load_curated

FF_HEADER = [
    "chain", "item", "grams", "kcal", "protein_g", "carbs_g", "sugars_g",
    "fat_g", "sat_fat_g", "fibre_g", "salt_g", "source_url", "checked_on",
]


def ff(**kw) -> dict:
    row = {
        "chain": "McDonald's", "item": "Big Mac", "grams": "219", "kcal": "503",
        "protein_g": "25", "carbs_g": "", "sugars_g": "", "fat_g": "", "sat_fat_g": "",
        "fibre_g": "", "salt_g": "", "source_url": "https://x", "checked_on": "2026-10-14",
    }
    row.update(kw)
    return row


def make_dir(
    path: Path,
    *,
    fastfood: list[dict] | None = None,
    portions: list[list] | None = None,
    dishes: list[dict] | None = None,
) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["alim_code", "alim_nom_eng"])
    ws.append([22000, "Egg, raw"])
    ws.append([13039, "Apple, raw"])
    wb.save(path / "ciqual_2025_en.xlsx")
    default_portions = [["13039", "1 medium apple", "182", "https://x"]]
    with open(path / "portions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["alim_code", "label_en", "grams", "source"])
        w.writerows(default_portions if portions is None else portions)
    with open(path / "fastfood.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, FF_HEADER)
        w.writeheader()
        w.writerows([ff()] if fastfood is None else fastfood)
    (path / "dishes.yaml").write_text(
        yaml.safe_dump([] if dishes is None else dishes, allow_unicode=True), encoding="utf-8"
    )
    return path


def test_blank_cell_is_unknown(tmp_path):
    data = load_curated(make_dir(tmp_path))
    values = data.fastfood[0].per_100g
    assert values[Nutrient.CARBOHYDRATE] is None
    assert values[Nutrient.ENERGY_KCAL] is not None


def test_per_item_to_per_100g(tmp_path):
    data = load_curated(make_dir(tmp_path))
    kcal = data.fastfood[0].per_100g[Nutrient.ENERGY_KCAL]
    assert isinstance(kcal, Decimal)
    assert kcal == Decimal("229.680")


def test_unknown_alim_code_rejected(tmp_path):
    d = make_dir(tmp_path, portions=[["99999", "1 thing", "10", "https://x"]])
    with pytest.raises(CuratedError) as e:
        load_curated(d)
    assert any("99999" in p for p in e.value.problems)


def test_nonpositive_grams_rejected(tmp_path):
    d = make_dir(tmp_path, portions=[["13039", "1 medium apple", "0", "https://x"]])
    with pytest.raises(CuratedError) as e:
        load_curated(d)
    assert any("grams" in p for p in e.value.problems)


def test_bad_date_rejected(tmp_path):
    with pytest.raises(CuratedError) as e:
        load_curated(make_dir(tmp_path, fastfood=[ff(checked_on="14/10/2026")]))
    assert any("checked_on" in p for p in e.value.problems)


def test_all_problems_listed_at_once(tmp_path):
    d = make_dir(
        tmp_path,
        fastfood=[ff(checked_on="nope"), ff(item="B", kcal="abc")],
        portions=[["99999", "x", "-1", "https://x"]],
        dishes=[{"name": "D", "ingredients": [{"alim_code": 1, "grams": 5}]}],
    )
    with pytest.raises(CuratedError) as e:
        load_curated(d)
    assert len(e.value.problems) >= 5


def test_item_without_grams_skipped_and_reported(tmp_path):
    data = load_curated(make_dir(tmp_path, fastfood=[ff(grams=""), ff(item="Cheeseburger")]))
    assert [r.item for r in data.fastfood] == ["Cheeseburger"]
    assert len(data.skipped) == 1 and "Big Mac" in data.skipped[0]
