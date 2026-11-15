from pathlib import Path

from app.importers.supermarket import BRANDS, read_snapshot


def test_french_supermarket_snapshot_covers_requested_brands_and_preserves_barcodes():
    path = Path(__file__).resolve().parents[2] / "catalogue_data" / "supermarket-france.csv.gz"
    rows = list(read_snapshot(path))
    assert len(rows) >= 20_000
    assert len({row["code"] for row in rows}) == len(rows)
    assert {"Monster", "Monster Energy", "Joker", "Tropicana", "McCain", "Findus",
            "E.Leclerc", "Eco+", "Marque Repère"} <= {row["brands"] for row in rows}
    assert all("en:france" in row["countries_tags"].split(",") for row in rows)
    assert any("mango loco" in row["product_name"].lower() for row in rows)
    assert any("leclerc" in row["stores"].lower() and row["brands"] not in BRANDS for row in rows)
