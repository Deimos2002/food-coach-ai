"""Curated food data importer: English names, household portions, fast-food items and dishes.

``load_curated`` parses and validates the data folder (pure, no database) and reports every
problem at once. ``import_curated`` writes it in the caller's transaction as one new catalogue
release, which first carries forward the active release's product states so Ciqual and Open
Food Facts products stay live.

Natural keys make re-imports idempotent: portions by (product, label), fast food by
(brand, name) as catalogue products whose nutrients go through ``add_source_revision``
(source ``label``), templates by name (items replaced). A blank nutrient cell is unknown,
never 0. Fast-food values are published per item and stored per 100 g.
"""

from __future__ import annotations

import csv
import datetime as dt
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
import sqlalchemy as sa
import yaml
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.engine import make_engine
from app.db.tables import meal_template_item, portion
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Derivation, Nutrient, NutrientValue, Qualifier, Range, Source
from app.importers.ciqual import _existing_product_id
from app.services.search import search_text_for

FASTFOOD_COLUMNS: dict[Nutrient, str] = {
    Nutrient.ENERGY_KCAL: "kcal",
    Nutrient.PROTEIN: "protein_g",
    Nutrient.CARBOHYDRATE: "carbs_g",
    Nutrient.SUGARS: "sugars_g",
    Nutrient.FAT: "fat_g",
    Nutrient.SATURATED_FAT: "sat_fat_g",
    Nutrient.FIBRE: "fibre_g",
    Nutrient.SALT: "salt_g",
}
_PER_100G_STEP = Decimal("0.001")


class CuratedError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class PortionRow:
    alim_code: str
    label: str
    grams: Decimal


@dataclass(frozen=True)
class FastFoodRow:
    chain: str
    item: str
    grams: Decimal
    per_100g: dict[Nutrient, Decimal | None]
    raw: dict[str, str]


@dataclass(frozen=True)
class TemplateRow:
    name: str
    name_en: str | None
    cuisine: str | None
    chain: str | None
    estimate: bool
    source_url: str | None
    ingredients: list[tuple[str, Decimal]]


@dataclass
class CuratedData:
    names: dict[str, str] = field(default_factory=dict)
    portions: list[PortionRow] = field(default_factory=list)
    fastfood: list[FastFoodRow] = field(default_factory=list)
    templates: list[TemplateRow] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportReport:
    names_set: int
    portions: int
    fastfood: int
    skipped: list[str]
    templates: int


def _positive(text: object) -> Decimal | None:
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_curated(directory: Path) -> CuratedData:
    directory = Path(directory)
    problems: list[str] = []
    data = CuratedData()

    ws = openpyxl.load_workbook(directory / "ciqual_2025_en.xlsx", read_only=True).worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    code_i, name_i = header.index("alim_code"), header.index("alim_nom_eng")
    for r in rows:
        if r[code_i] is not None and r[name_i]:
            data.names[str(r[code_i]).strip()] = str(r[name_i]).strip()

    for n, r in enumerate(_read_csv(directory / "portions.csv"), start=2):
        where = f"portions.csv line {n}"
        code = r["alim_code"].strip()
        grams = _positive(r["grams"])
        if code not in data.names:
            problems.append(f"{where}: unknown alim_code {code!r}")
        if grams is None:
            problems.append(f"{where}: grams must be a positive number, got {r['grams']!r}")
        if not r["label_en"].strip():
            problems.append(f"{where}: empty label_en")
        if code in data.names and grams is not None and r["label_en"].strip():
            data.portions.append(PortionRow(code, r["label_en"].strip(), grams))

    for n, r in enumerate(_read_csv(directory / "fastfood.csv"), start=2):
        where = f"fastfood.csv line {n}"
        bad = len(problems)
        try:
            dt.date.fromisoformat(r["checked_on"])
        except ValueError:
            problems.append(f"{where}: bad checked_on {r['checked_on']!r}")
        raw_values: dict[Nutrient, Decimal | None] = {}
        for nutrient, col in FASTFOOD_COLUMNS.items():
            cell = r[col].strip()
            if not cell:
                raw_values[nutrient] = None
                continue
            try:
                value = Decimal(cell)
                if not value.is_finite() or value < 0:
                    raise InvalidOperation
            except InvalidOperation:
                problems.append(f"{where}: bad {col} {cell!r}")
                continue
            raw_values[nutrient] = value
        if not r["grams"].strip():
            data.skipped.append(f"{r['chain']}: {r['item']} (no published grams)")
            continue
        grams = _positive(r["grams"])
        if grams is None:
            problems.append(f"{where}: grams must be a positive number, got {r['grams']!r}")
        if len(problems) == bad:
            per_100 = {
                n_: None if v is None else (v * 100 / grams).quantize(_PER_100G_STEP)
                for n_, v in raw_values.items()
            }
            data.fastfood.append(FastFoodRow(r["chain"], r["item"], grams, per_100, r))

    dishes = yaml.safe_load((directory / "dishes.yaml").read_text(encoding="utf-8")) or []
    for d in dishes:
        where = f"dishes.yaml {d.get('name')!r}"
        ingredients: list[tuple[str, Decimal]] = []
        ok = True
        for ing in d.get("ingredients", []):
            code = str(ing["alim_code"]).strip()
            grams = _positive(ing["grams"])
            if code not in data.names:
                problems.append(f"{where}: unknown alim_code {code!r}")
                ok = False
            if grams is None:
                problems.append(f"{where}: grams must be a positive number, got {ing['grams']!r}")
                ok = False
            elif code in data.names:
                ingredients.append((code, grams))
        if ok:
            data.templates.append(
                TemplateRow(
                    d["name"], d.get("name_en"), d.get("cuisine"), d.get("chain"),
                    bool(d.get("estimate", False)), d.get("source_url"), ingredients,
                )
            )

    if problems:
        raise CuratedError(problems)
    return data


def _fastfood_values(row: FastFoodRow) -> dict[Nutrient, NutrientValue]:
    return {
        n: NutrientValue.unknown(Source.LABEL)
        if v is None
        else NutrientValue(
            True, Range.point(v), Qualifier.NONE, Source.LABEL, Derivation.CONVERTED
        )
        for n, v in row.per_100g.items()
    }


def _upsert_portion(conn: Connection, product_id: int, label: str, grams: Decimal) -> None:
    updated = conn.execute(
        sa.update(portion)
        .where(portion.c.product_id == product_id, portion.c.label == label)
        .values(amount=grams, kind="serving")
    ).rowcount
    if not updated:
        conn.execute(
            sa.insert(portion).values(
                product_id=product_id, label=label, amount=grams, kind="serving"
            )
        )


def import_curated(conn: Connection, data: CuratedData) -> ImportReport:
    skipped = list(data.skipped)
    release_id = catalogue.create_release(conn, "curated food data")

    ids: dict[str, int | None] = {c: _existing_product_id(conn, c) for c in data.names}

    names_set = 0
    for code, name_en in data.names.items():
        pid = ids[code]
        if pid is None:
            continue
        name, brand = conn.execute(
            sa.text("SELECT name, brand FROM product WHERE id = :p"), {"p": pid}
        ).one()
        conn.execute(
            sa.text("UPDATE product SET name_en = :en, search_text = :st WHERE id = :p"),
            {"en": name_en, "st": search_text_for(name, brand, [name_en]), "p": pid},
        )
        names_set += 1

    portions = 0
    for p in data.portions:
        pid = ids[p.alim_code]
        if pid is None:
            skipped.append(f"portion {p.label!r}: alim_code {p.alim_code} not in catalogue")
            continue
        _upsert_portion(conn, pid, p.label, p.grams)
        portions += 1

    for f in data.fastfood:
        pid = conn.execute(
            sa.text(
                "SELECT id FROM product WHERE owner = 'catalogue' AND brand = :b AND name = :n"
            ),
            {"b": f.chain, "n": f.item},
        ).scalar_one_or_none()
        if pid is None:
            pid = catalogue.insert_product(
                conn, owner="catalogue", kind="product", name=f.item, brand=f.chain,
                basis_unit=Unit.G, state="as_sold",
            )
        outcome = catalogue.add_source_revision(
            conn, pid, Source.LABEL, f"{f.chain}:{f.item}", f.raw, _fastfood_values(f)
        )
        if outcome.live_revision_id is not None:
            catalogue.set_product_state(
                conn, release_id, pid, ProductState(Status.ACTIVE), outcome.live_revision_id
            )
        _upsert_portion(conn, pid, f"1 {f.item}", f.grams)

    templates = 0
    for t in data.templates:
        missing = [c for c, _ in t.ingredients if ids[c] is None]
        if missing:
            skipped.append(f"template {t.name!r}: alim_code {missing[0]} not in catalogue")
            continue
        fields = {
            "name_en": t.name_en, "cuisine": t.cuisine, "chain": t.chain,
            "estimate": t.estimate, "source_url": t.source_url,
        }
        template_id = conn.execute(
            sa.text(
                "INSERT INTO meal_template (name, name_en, cuisine, chain, estimate, source_url) "
                "VALUES (:name, :name_en, :cuisine, :chain, :estimate, :source_url) "
                "ON CONFLICT (name) DO UPDATE SET name_en = EXCLUDED.name_en, "
                "cuisine = EXCLUDED.cuisine, chain = EXCLUDED.chain, "
                "estimate = EXCLUDED.estimate, source_url = EXCLUDED.source_url RETURNING id"
            ),
            {"name": t.name, **fields},
        ).scalar_one()
        conn.execute(sa.delete(meal_template_item).where(meal_template_item.c.template_id == template_id))
        for position, (code, grams) in enumerate(t.ingredients):
            conn.execute(
                sa.insert(meal_template_item).values(
                    template_id=template_id, product_id=ids[code], grams=grams, position=position
                )
            )
        templates += 1

    catalogue.activate_release(conn, release_id)
    return ImportReport(names_set, portions, len(data.fastfood), skipped, templates)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.importers.curated <data-dir>", file=sys.stderr)
        return 2
    try:
        data = load_curated(Path(argv[0]))
    except CuratedError as exc:
        for problem in exc.problems:
            print(problem, file=sys.stderr)
        return 1

    from app.config import get_settings

    engine = make_engine(get_settings().database_url)
    try:
        with engine.begin() as conn:
            report = import_curated(conn, data)
    finally:
        engine.dispose()

    print(
        f"Curated import complete: {report.names_set} English names, {report.portions} portions, "
        f"{report.fastfood} fast-food items, {report.templates} templates."
    )
    for line in report.skipped:
        print(f"  skipped: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
