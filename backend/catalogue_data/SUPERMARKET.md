# French supermarket catalogue

`supermarket-france.csv.gz` is a pinned Open Food Facts export downloaded on
**2026-10-15** from [Mirabelle](https://mirabelle.openfoodfacts.org/products),
Open Food Facts' official CSV query service. It contains **39,701 source records**
marked `en:france`: **17,436 Leclerc-tagged records**, plus the selected popular
brands, deduplicated by barcode. These include Monster/Monster Energy, Red Bull, Joker, Tropicana,
Innocent, Granini, Oasis, McCain, Findus, E.Leclerc, Marque Repère, Eco+, and other
common supermarket brands. The exact brand spellings and export columns live in
`app/importers/supermarket.py` (`BRANDS` and `download_snapshot`). Its selection is
French-market products where the store field contains `leclerc` or the brand is
one of those spellings. The source export includes 4,479 distinct brand strings.

The pinned snapshot supplies **23,616 usable food/drink products**, including
**14,074 Leclerc-tagged products**, across 4,004 brand labels. The importer reports
16,085 skipped records, mostly missing a declared gram/ml basis. These counts are
for source records, not individual store availability.

The gzip snapshot's SHA-256 is
`7d1f5ed52fd366a3a2340b3b3daeb8fe38cb04c2175969fe5ac5ef27e44b43a0`.
Each row preserves its source barcode, URL, modification date, declared quantity,
French-market tags, category, source quality errors, and available nutrient fields.
CSV decimal strings are never converted through floating point.

This is **not Leclerc's complete inventory or a stock/availability feed**. The
selection uses store tags and exact brand names in Open Food Facts, not Leclerc's
official inventory. Unlisted products, untagged brands outside the selection, and
records without usable quantities can be absent. French-market tags can also include imported package variants:
match the barcode and package size to the item in hand.

## Import and refresh locally

With the backend virtual environment active and `DATABASE_URL` configured for the
local development database, run from `backend/`:

```powershell
python -m app.importers.supermarket catalogue_data/supermarket-france.csv.gz --skipped-report data/supermarket-skipped.txt
```

The importer creates a new shared catalogue release, carries existing products
forward, and deduplicates by catalogue barcode. It never changes user-owned
products. Nutrition changes use the existing revision-review rules; retired or
merged catalogue identities retain their states. Unknown nutrients stay unknown;
reported zero remains known zero. Declared package amounts become package portions.
English category terms are searchable without replacing the source product name.

Records without a name, French-market tag, valid barcode, or declared gram/ml
basis are skipped. Records flagged with source quality errors or invalid package
amounts are also skipped, as are recognized non-food and pet-food categories.
A barcode's existing basis cannot change on refresh.
The command prints counts and can write the complete skip report to the ignored
`data/` directory. Products whose nutrition is entirely absent may still be
imported, with every nutrient unknown.

To download a fresh snapshot without modifying the database:

```powershell
python -m app.importers.supermarket --download data/supermarket-refresh.csv.gz
python -m app.importers.supermarket data/supermarket-refresh.csv.gz --skipped-report data/supermarket-refresh-skipped.txt
```

The downloader requests the CSV export once, with streaming enabled, rather than
crawling individual product pages. Failed responses and malformed exports do not
replace an existing snapshot. The importer also accepts other Open Food Facts CSV
exports with the required columns; this permits broader future selections without
changing the database schema.

## Source and attribution

Data: **Open Food Facts**, under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/).
Individual database contents are covered by the
[Database Contents License](https://opendatacommons.org/licenses/dbcl/).
Keep this attribution and applicable database-license obligations when sharing
the dataset. No product images are included.

Official references:

- [Downloading a country or brand export](https://support.openfoodfacts.org/help/fr-fr/12-donnees-api/89-je-souhaite-un-export-csv-pour-un-pays-une-marque-en-particulier)
- [API usage and bulk-export guidance](https://openfoodfacts.github.io/openfoodfacts-server/api/)

Open Food Facts is contributor data, not independently verified label data. Values
are stored as `off_contributor`, never promoted to verified label values. Source
formulations can be older than today's package; `last_modified_datetime` is retained
in the raw source record. Estimated nutrient fields are not imported or used to
fill missing published fields.

The shared OFF decoder converts `caffeine_100g` from grams to the app's milligrams
using `Decimal(1000)` and records a converted derivation. OFF's `alcohol_100g` is
alcohol by volume, not grams: positive values remain unknown in the app; an explicit
zero remains known zero. See the [official CSV field definitions](https://github.com/openfoodfacts/openfoodfacts-server/blob/main/html/data-fields.txt).
