"""Import an Open Food Facts CSV export as shared, provenance-aware catalogue products."""

import argparse
import csv
import gzip
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.tables import active_release, portion, product, product_state, profile_revision
from app.domain.identity import ProductState, Status
from app.domain.revisions import nutrition_hash
from app.domain.values import Source
from app.importers.off import OFF_FIELDS, _off_value, detect_basis, off_values
from app.services.search import search_text_for

BRANDS = (
    "Monster", "Monster Energy", "Red Bull", "Joker", "Tropicana", "Innocent", "innocent",
    "Granini", "Oasis", "McCain", "Mc Cain", "Findus", "Marque Repère", "Marque Repere",
    "Eco+", "Eco +", "E.Leclerc", "E. Leclerc", "Leclerc", "Bonduelle", "Fleury Michon",
    "Panzani", "Barilla", "Lustucru", "Danone", "Lactel", "Président", "Herta", "LU",
    "Bonne Maman", "Andros", "Bénénuts", "Lay's", "Lays", "Pringles", "Coca-Cola", "Pepsi",
    "Orangina", "Lipton", "Fanta", "Schweppes", "Cristaline", "Evian", "Volvic", "Perrier",
)


def download_snapshot(client: httpx.Client, path: Path) -> int:
    fields = [
        "code", "url", "last_modified_datetime", "product_name", "brands", "quantity",
        "product_quantity", "countries_tags", "stores", "main_category_en",
        "data_quality_errors_tags", *OFF_FIELDS.values(),
    ]
    columns = ",".join(f'"{field}"' for field in fields)
    brands = ",".join("'" + brand.replace("'", "''") + "'" for brand in BRANDS)
    query = (
        f"select {columns} from [all] where (brands in ({brands}) "
        "or lower(stores) like '%leclerc%') "
        "and countries_tags like '%en:france%' order by code"
    )
    response = client.get(
        "https://mirabelle.openfoodfacts.org/products.csv",
        params={"sql": query, "_stream": "on", "_size": "max"},
        headers={"User-Agent": "FoodCoach/0.1 (personal nutrition catalogue)"},
    )
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        temporary.write_bytes(
            gzip.compress(response.content, mtime=0) if path.suffix == ".gz" else response.content
        )
        count = sum(1 for _ in read_snapshot(temporary))
        if not count:
            raise ValueError("Open Food Facts export contains no products")
        temporary.replace(path)
        return count
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class ImportReport:
    imported: int
    skipped: list[str]


def read_snapshot(path: Path) -> Iterator[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if not {"code", "product_name", "quantity", "countries_tags"} <= set(reader.fieldnames or []):
            raise ValueError("Missing required Open Food Facts CSV columns")
        for line, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed Open Food Facts CSV row {line}")
            yield row


def import_supermarket(conn: Connection, rows: Iterable[dict[str, str]]) -> ImportReport:
    release = catalogue.create_release(conn, "Open Food Facts supermarket snapshot")
    existing = {code: (pid, unit) for code, pid, unit in conn.execute(sa.select(
        product.c.barcode, product.c.id, product.c.basis_unit,
    ).where(
        product.c.owner == "catalogue", product.c.barcode.is_not(None),
    ))}
    states = catalogue.load_states(conn)
    live_hashes = dict(conn.execute(sa.select(
        product_state.c.product_id, profile_revision.c.nutrition_hash,
    ).join(profile_revision, profile_revision.c.id == product_state.c.active_revision_id).where(
        product_state.c.release_id == sa.select(active_release.c.release_id).scalar_subquery(),
    )).all())
    packages = dict(conn.execute(sa.select(portion.c.product_id, portion.c.amount).where(
        portion.c.label == "package",
    )).all())
    imported = 0
    skipped = []
    for row in rows:
        code = (row.get("code") or "").strip()
        name = (row.get("product_name") or "").strip()
        category = (row.get("main_category_en") or "").lower().replace("-", " ")
        basis = detect_basis(row.get("quantity"))
        package = _off_value(row.get("product_quantity"))
        reason = None
        if not code.isascii() or not code.isdecimal() or not 8 <= len(code) <= 14:
            reason = "missing or invalid barcode"
        elif not name:
            reason = "missing name"
        elif "en:france" not in (row.get("countries_tags") or "").split(","):
            reason = "not marked for the French market"
        elif category in {"soap", "soaps"} or any(term in category for term in (
            "detergent", "pet food", "dog food", "cat food", "shampoo", "toothpaste", "cosmetic",
        )):
            reason = "non-food category"
        elif basis is None:
            reason = "basis not declared in quantity"
        elif row.get("data_quality_errors_tags"):
            reason = "source quality error"
        elif row.get("product_quantity") and (
            not package.known or package.range.lower <= 0
        ):
            reason = "invalid package quantity"
        elif code in existing and existing[code][1] != basis.value:
            reason = "existing barcode has a different basis"
        if reason:
            skipped.append(f"{code or '(no barcode)'}: {reason}")
            continue
        pid = existing.get(code, (None, None))[0]
        if pid is None:
            pid = catalogue.insert_product(
                conn, owner="catalogue", kind="product", name=name,
                brand=row.get("brands"), basis_unit=basis,
                state="as_sold", barcode=code,
            )
            existing[code] = (pid, basis.value)
            if row.get("main_category_en"):
                conn.execute(sa.update(product).where(product.c.id == pid).values(
                    search_text=search_text_for(name, row.get("brands"), [row["main_category_en"]]),
                ))
        values = off_values({field: row.get(field) for field in OFF_FIELDS.values()})
        # The new release already carries unchanged states; avoid per-product reads on refresh.
        if live_hashes.get(pid) == nutrition_hash(values) and (
            not package.known or package.range.lower == packages.get(pid)
        ):
            imported += 1
            continue
        outcome = catalogue.add_source_revision(
            conn, pid, Source.OFF_CONTRIBUTOR, code, row, values,
        )
        catalogue.set_product_state(
            conn, release, pid, states.get(pid, ProductState(Status.ACTIVE)),
            outcome.live_revision_id,
        )
        if package.known and package.range.lower > 0:
            amount = package.range.lower
            updated = conn.execute(sa.update(portion).where(
                portion.c.product_id == pid, portion.c.label == "package",
            ).values(amount=amount, kind="package")).rowcount
            if not updated:
                conn.execute(sa.insert(portion).values(
                    product_id=pid, label="package", amount=amount, kind="package",
                ))
        imported += 1
    catalogue.activate_release(conn, release)
    return ImportReport(imported, skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Open Food Facts CSV or CSV.gz snapshot")
    parser.add_argument("--download", action="store_true", help="Download a fresh French-brand export; do not import")
    parser.add_argument("--skipped-report", type=Path, help="Write every skipped barcode and reason")
    args = parser.parse_args()
    if args.download:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            count = download_snapshot(client, args.snapshot)
        print(f"Downloaded {count} source records to {args.snapshot}")
        return
    from app.config import get_settings
    from app.db.engine import make_engine

    engine = make_engine(get_settings().database_url)
    try:
        with engine.begin() as conn:
            report = import_supermarket(conn, read_snapshot(args.snapshot))
    finally:
        engine.dispose()
    print(f"Imported {report.imported} products; skipped {len(report.skipped)} records.")
    for reason, count in Counter(line.partition(": ")[2] for line in report.skipped).items():
        print(f"  {reason}: {count}")
    if args.skipped_report:
        args.skipped_report.parent.mkdir(parents=True, exist_ok=True)
        args.skipped_report.write_text("\n".join(report.skipped) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
