"""Ciqual 2025 importer: loads the ANSES Ciqual generic-food table into the catalogue.

One run creates one release: every row of the XLSX becomes (or updates) one catalogue
product keyed by its Ciqual food code (``alim_code``), with its nutrient profile
recorded as a source revision and placed live in the new release, which is then
activated.

Re-running is idempotent in spirit: a second run against unchanged data reuses the
same products (matched by ``alim_code`` as the external id) and the same revisions via
``catalogue.add_source_revision``'s NOOP/ADOPT dedup -- it never creates duplicate
products.

A cell that fails to parse (``parse_ciqual_cell`` raising ``ValueError``) does not abort
the import: that nutrient is stored as unknown for that food, the failure is counted,
and a summary of failure counts per column is printed at the end.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from app.db import catalogue
from app.db.engine import make_engine
from app.domain.ciqual_parse import parse_ciqual_cell
from app.domain.identity import ProductState, Status
from app.domain.quantity import Unit
from app.domain.values import Nutrient, NutrientValue, Source
from app.importers.ciqual_columns import ALIM_CODE_COLUMN, ALIM_NAME_COLUMN, CIQUAL_COLUMNS

_AS_PREPARED_MARKERS = ("cuit", "préparé", "reconstitué")


def _state_for_name(name: str) -> str:
    lowered = name.lower()
    if any(marker in lowered for marker in _AS_PREPARED_MARKERS):
        return "as_prepared"
    return "as_sold"


def _read_rows(xlsx_path: Path) -> tuple[list[str], list[tuple[Any, ...]]]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = list(next(rows))
    data = list(rows)
    wb.close()
    return header, data


def _parse_row(
    header: list[str],
    row: tuple[Any, ...],
    failure_counts: Counter[str],
) -> tuple[str, str, dict[Nutrient, NutrientValue], dict[str, Any]]:
    by_column = dict(zip(header, row, strict=True))
    alim_code = str(by_column[ALIM_CODE_COLUMN])
    name = str(by_column[ALIM_NAME_COLUMN])

    values: dict[Nutrient, NutrientValue] = {}
    raw: dict[str, Any] = {}
    for nutrient, column in CIQUAL_COLUMNS.items():
        cell = by_column.get(column)
        raw[column] = cell
        try:
            values[nutrient] = parse_ciqual_cell(cell)
        except ValueError:
            failure_counts[column] += 1
            values[nutrient] = NutrientValue.unknown(Source.CIQUAL)

    return alim_code, name, values, raw


def _existing_product_id(conn: Connection, alim_code: str) -> int | None:
    """The catalogue product already linked to this Ciqual food code, if any.

    Matched through its most recent source record's ``external_id`` (spec/Task 8:
    source records are the key-bearing records; products carry no external id of
    their own). ``ORDER BY sr.id DESC`` relies on ``source_record.id`` being a
    monotonically increasing surrogate key, so the most recently inserted row for this
    ``alim_code`` is always the last one created, not merely the largest by any other
    ordering.
    """
    return conn.execute(
        sa.text(
            "SELECT sr.product_id FROM source_record sr "
            "JOIN product p ON p.id = sr.product_id "
            "WHERE sr.source = 'ciqual' AND sr.external_id = :code AND p.owner = 'catalogue' "
            "ORDER BY sr.id DESC LIMIT 1"
        ),
        {"code": alim_code},
    ).scalar_one_or_none()


def import_ciqual(conn: Connection, xlsx_path: Path) -> int:
    """Import the Ciqual 2025 table from ``xlsx_path`` as one new catalogue release.

    Returns the new release id. Prints a per-column count of cells that failed to
    parse (and were stored as unknown) when any occurred.
    """
    header, rows = _read_rows(xlsx_path)
    failure_counts: Counter[str] = Counter()

    release_id = catalogue.create_release(conn, f"ciqual import: {xlsx_path.name}")

    for row in rows:
        alim_code, name, values, raw = _parse_row(header, row, failure_counts)

        product_id = _existing_product_id(conn, alim_code)
        if product_id is None:
            product_id = catalogue.insert_product(
                conn,
                owner="catalogue",
                kind="product",
                name=name,
                brand=None,
                basis_unit=Unit.G,
                state=_state_for_name(name),
            )

        outcome = catalogue.add_source_revision(
            conn, product_id, Source.CIQUAL, alim_code, raw, values
        )
        if outcome.live_revision_id is not None:
            catalogue.set_product_state(
                conn, release_id, product_id, ProductState(Status.ACTIVE), outcome.live_revision_id
            )

    catalogue.activate_release(conn, release_id)

    if failure_counts:
        print("Ciqual import: cell parse failures (stored as unknown) by column:")
        for column, count in failure_counts.most_common():
            print(f"  {column!r}: {count}")

    return release_id


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.importers.ciqual <path-to-xlsx>", file=sys.stderr)
        return 2
    xlsx_path = Path(argv[0])

    from app.config import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            import_ciqual(conn, xlsx_path)
            product_count = conn.execute(
                sa.text("SELECT count(*) FROM product WHERE owner = 'catalogue'")
            ).scalar_one()
    finally:
        engine.dispose()

    print(f"Ciqual import complete: {product_count} catalogue products.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
