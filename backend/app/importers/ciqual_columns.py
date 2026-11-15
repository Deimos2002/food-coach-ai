"""Column mapping for the Ciqual 2025 XLSX table (ANSES, ``Table Ciqual 2025_FR_2025_11_03.xlsx``).

Exact header strings as they appear in row 1 of the "composition nutritionnelle" sheet
(multi-line cells; newlines are literal `\n` within the header text). Nutrients with no
corresponding Ciqual column (caffeine; polyols' erythritol sub-component) are omitted
from this mapping on purpose, so the importer stores them as unknown rather than
guessing or mis-mapping them.

- Energy uses the EU regulation 1169/2011 kcal column (not the Jones-factor column).
- Protein uses the N x 6.25 column, not the Jones-factor column that is also present in
  the table: Ciqual's own EU 1169/2011 energy column is computed from N x 6.25 protein,
  and EU nutrition labels and Open Food Facts also use N x 6.25. Using the Jones-factor
  protein here would break the energy/protein consistency check (spec §4.6) and make
  Ciqual foods incomparable with branded (label/OFF) products.
- Salt is the "Sel chlorure de sodium" column.
"""

from __future__ import annotations

from app.domain.values import Nutrient

#: Identifier / name columns used by the importer outside of nutrient values.
ALIM_CODE_COLUMN = "alim_code"
ALIM_NAME_COLUMN = "alim_nom_fr"

CIQUAL_COLUMNS: dict[Nutrient, str] = {
    Nutrient.ENERGY_KCAL: "Energie,\nRèglement\nUE N°\n1169\n2011 (kcal\n100 g)",
    Nutrient.PROTEIN: "Protéines,\nN x 6.25\n(g\n100 g)",
    Nutrient.CARBOHYDRATE: "Glucides\n(g\n100 g)",
    Nutrient.SUGARS: "Sucres\n(g\n100 g)",
    Nutrient.FAT: "Lipides\n(g\n100 g)",
    Nutrient.SATURATED_FAT: "AG\nsaturés\n(g\n100 g)",
    Nutrient.FIBRE: "Fibres\nalimentaires\n(g\n100 g)",
    Nutrient.SALT: "Sel\nchlorure\nde\nsodium\n(g\n100 g)",
    Nutrient.ALCOHOL: "Alcool\n(éthanol)\n(g\n100 g)",
    Nutrient.POLYOLS: "Polyols\ntotaux\n(g\n100 g)",
    # Nutrient.CAFFEINE: no column in Ciqual 2025 -> stored as unknown.
    # Nutrient.ERYTHRITOL: no column in Ciqual 2025 -> stored as unknown.
}
