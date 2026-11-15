import gzip

import httpx
import pytest
import respx

from app.importers.supermarket import download_snapshot, read_snapshot


def test_read_gzip_csv_preserves_barcode_and_decimal_strings(tmp_path):
    path = tmp_path / "foods.csv.gz"
    path.write_bytes(gzip.compress(
        b"code,product_name,quantity,countries_tags,sugars_100g\n"
        b"0001234567890,Juice,1 l,en:france,11.123456789012345678\n"
    ))
    row, = read_snapshot(path)
    assert row["code"] == "0001234567890"
    assert row["sugars_100g"] == "11.123456789012345678"


def test_read_rejects_an_error_page_instead_of_treating_it_as_empty_data(tmp_path):
    path = tmp_path / "foods.csv"
    path.write_text("<html>Temporarily unavailable</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV columns"):
        list(read_snapshot(path))


@respx.mock
def test_download_uses_french_bulk_export_and_writes_a_readable_snapshot(tmp_path):
    route = respx.get("https://mirabelle.openfoodfacts.org/products.csv").respond(
        200, text="code,product_name,quantity,countries_tags\n0001234567890,Juice,1 l,en:france\n",
    )
    path = tmp_path / "foods.csv.gz"
    with httpx.Client() as client:
        assert download_snapshot(client, path) == 1
    params = route.calls.last.request.url.params
    assert "en:france" in params["sql"]
    assert "Monster Energy" in params["sql"] and "McCain" in params["sql"]
    assert "lower(stores) like '%leclerc%'" in params["sql"]
    assert params["_stream"] == "on"
    row, = read_snapshot(path)
    assert row["code"] == "0001234567890"


@pytest.mark.parametrize("status,body,error", [
    (503, "Unavailable", httpx.HTTPStatusError),
    (200, "<html>Unavailable</html>", ValueError),
    (200, "code,product_name,quantity,countries_tags\n0001234567890,Juice\n", ValueError),
])
@respx.mock
def test_failed_download_never_replaces_a_previous_snapshot(tmp_path, status, body, error):
    respx.get("https://mirabelle.openfoodfacts.org/products.csv").respond(status, text=body)
    path = tmp_path / "foods.csv.gz"
    path.write_bytes(b"previous snapshot")
    with httpx.Client() as client, pytest.raises(error):
        download_snapshot(client, path)
    assert path.read_bytes() == b"previous snapshot"
