# Catalogue data: sources and method

Everything here was fetched on 2026-10-14 unless stated. No LLM produced a number; values were read from the pages below
and converted with the rules in the "Derivations" section. Blank nutrient cell = unknown, never 0.

## ciqual_2025_en.xlsx
Official ANSES Ciqual 2025 table, English edition (`Table Ciqual 2025_ENG_2025_11_03.xlsx`, same release 2025-11-03 as the French file).
Downloaded from `https://ciqual.anses.fr/cms/sites/default/files/inline-files/Table%20Ciqual%202025_ENG_2025_11_03.xlsx`
(the download page https://ciqual.anses.fr/ links it). SHA-256 `021d80f202fa184d7d1e6c6283da7d94a408fef0fbca0c28293243673c0778b0`.
Sheet `food composition`, 3484 foods, `alim_code` set identical to the French edition (checked). Column `alim_nom_eng` holds the English name.

## portions.csv (157 rows)
Gram weights of household measures from the USDA FoodData Central **SR Legacy** CSV release (April 2018),
`https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip`, file `food_portion.csv`
(`gram_weight` / `amount`). Each row's `source` is the FDC page of the SR item used
(`https://fdc.nal.usda.gov/food-details/<fdc_id>/nutrients`). Rows whose label does not start with "1" use the SR measure as published
(for example "1/4 block firm tofu (81 g)").
Pairing a Ciqual food with an SR food is my judgement of "same food" (for example Ciqual `Apple, flesh and skin, raw` with SR
`Apples, raw, with skin`). It is a weight-of-a-measure, not a nutrient value, so the Ciqual composition is unaffected.
Known looser pairs: cornflakes (SR brand Ralston), whole-wheat slice (Ciqual French wholemeal bread), cooked salmon (SR farmed Atlantic).

## fastfood.csv (60 rows) and published values
All values are the chain's own French figures. Per-portion values are entered as published; the importer converts to per 100 g.
`grams` is NOT published by McDonald's, Burger King, KFC, Quick or Five Guys (PDF has no weights) on these pages. It is derived as
`portion kJ / per-100 g kJ x 100` (kcal ratio when kJ is absent), rounded to 1 g. Check: Quick's older 2023 declaration publishes real
weights (Long Chicken 204 g, Giant Original 184 g, Mega Giant 291 g) and the derivation reproduces them exactly. Derived weights carry about +-1 %.

| Chain | Rows | Source (exact URLs in each row) | Notes |
|---|---|---|---|
| McDonald's | 15 | `https://www.mcdonalds.fr/nos-produits/...` product pages, table "Pour 100 g / Par produit" | includes fibre. Product names from the page URL. |
| Burger King | 7 | `https://www.burgerking.fr/produit/<slug>` "Par portion / Pour 100 g" | no fibre published (blank). "sucres rapides" entered as sugars. |
| KFC | 3 | `https://www.kfc.fr/notre-carte/produits-a-l-unite/<slug>` "100g / Portion" | only Colonel Original, Colonel Bacon, 5 Tenders had a populated table. `<0,5` sugars left blank. |
| Quick | 21 | `https://assets.quick.fr/Declaration_Nutritionnelle_QUICK_2025_FRANCE_TF_10_2025_9a1bffa965.pdf`, valid from 2025-12-09 | scanned PDF read by OCR; rows kept only when the kJ-derived and kcal-derived weights agree within 2 %. |
| Five Guys | 14 | `https://www.fiveguys.fr/wp-content/uploads/sites/32/2026/09/FGFR_FOH_allergen_ingredient_nutrition_LF_LW_A4_20260922-2.pdf` | burgers, fries sizes, loaded fries. Row-to-name mapping of the burger block checked against protein and cheese/bacon increments. |

Not entered (no usable published values or weights): KFC items other than the three above (the product pages render an empty table, the
Nutri-Score PDF `https://d1c35e6d994yha.cloudfront.net/articles/55567/kfc-ok.pdf` gives kcal only); Burger King sides and nuggets
(product-page slugs not found); McDonald's drinks, sauces, desserts, McFlurry (page has no table); Subway (kcal only, see dishes).
KFC "Moyenne frite" showed identical 100 g and portion columns, so it was left out.

## dishes.yaml
### Recipes (43, non-chain)
Ingredient lists and yields come from the JSON-LD of the BBC Good Food recipe in each entry's `source_url`
(`https://www.bbcgoodfood.com/recipes/<slug>`). `grams` are per serving (recipe total / stated yield). Conversions I applied:
1 tbsp oil 13.5 g, butter 14.2 g, flour 8 g, sugar 12.5 g (tsp 4.2 g), 1 ml milk 1.03 g, cream/wine/stock 1 g/ml, egg 50 g, onion 110 g,
carrot 61 g (large 72), garlic clove 3 g, medium tomato 123 g (large 182), pepper 119 g, small courgette 118 g, celery stick 40 g,
tortilla 49 g, raw chicken breast 175 g, can of beans or tuna counted drained (beans 240 g, tuna 105-120 g), potato "large" 250 g,
baguette slice 25 g, bread slice 30 g (these use the SR weights from portions.csv where they exist).
Choices: Ciqual raw/average entries; pecorino replaced by parmesan; soured cream, spices, herbs, dried porcini, spring onion, anchovy and
paste/chutney omitted (no suitable Ciqual entry or negligible); mussels counted as 150 g cooked flesh per portion (about 30 % of shell weight);
poutine fries use Ciqual deep-fried fries at 60 % of the raw potato weight; lamb mince uses `Lamb, leg, raw`; paella seafood mix uses boiled prawns.
Skipped on purpose: kedgeree (no Ciqual smoked haddock), easy chicken curry (no boneless thigh entry).
Ciqual composes its own energy per food; I did not compare dish kcal with the BBC page values.

### Estimated chain items (`chain:` + `estimate: true`, 14 items)
These chains publish no per-item nutrition. Ingredient lists are the menu wording; the gram figures are my assumptions.
| Chain | Items | Menu page | Calibration |
|---|---|---|---|
| O'Tacos | O'classic (M) | https://o-tacos.com/fr/products/oclassic | CLCV lab study (23 May 2023, `https://www.clcv.org/storage/app/media/DP_tacos_Coorection.pdf`): size M weighs 442 g at 267 kcal/100 g (1180 kcal). Estimate: 440 g, 1195 kcal. |
| Chamas Tacos | Le Typik, L'Indien, Le Veggie (M) | https://chamas-tacos.com/carte | CLCV: size M 466 g at 283 kcal/100 g (1319 kcal). Estimates: 465 g / 1308 kcal, 465 g / 1240 kcal, 520 g / 1250 kcal. |
| G La Dalle | Cheese Burger, Big | https://g-ladalle.com/product/g1/ and https://g-ladalle.com/product/big/ | steak weight 45 g is on the page. Rest assumed. |
| Tasty Crousty | Crousty poulet | https://tastycrousty.io/menu | fr.wikipedia.org/wiki/Tasty_Crousty (search snippet): 400-500 g and 900-1300 kcal. Estimate: 425 g, 1118 kcal. |
| Subway | Texan Steakhouse, Supreme Steak & Cheese, Chicken Bella Mozza, Deli Pastrami (SUB 15 cm) | Subway FR nutrition PDF `https://sasubwaystrapiprod.blob.core.windows.net/strapi-uploads/assets/A2_NUTRISCORE_ALLERGENNES_2024_Dec_20708559b2.pdf` | published kcal 434, 416, 397, 307. Estimates (Ciqual energy): 411, 452, 444, 305 kcal. |

## Chains and restaurant locators (presence in Lille and/or Toulouse)
No halal tag is stored anywhere in the data. Where a chain's own locator was reachable, the URL is listed.
- McDonald's: Lille https://www.mcdonalds.fr/restaurants/mcdonalds-lille-centre/30 ; Toulouse https://www.mcdonalds.fr/restaurants/mcdonalds-toulouse-capitole/22
- Burger King: Lille https://www.burgerking.fr/restaurants/lille-euralille , https://www.burgerking.fr/restaurants/lille-nationale
- KFC: Lille https://www.kfc.fr/nos-restaurants/kfc-lille-flandres ; Toulouse https://www.kfc.fr/nos-restaurants/kfc-toulouse-centre-ville (both from https://www.kfc.fr/sitemap.xml)
- Quick: Lille https://www.quick.fr/restaurants/lille-lillenium , https://www.quick.fr/restaurants/euralille
- Subway: Lille https://restaurants.subwayfrance.fr/fr/hauts-de-france/nord/lille
- Five Guys: Lille https://restaurants.fiveguys.fr/hauts-de-france/56-rue-de-b%C3%A9thune
- O'Tacos: Lille https://restaurants.o-tacos.com/fr-en/france/hauts-de-france/nord/lille/lille-rue-des-postes
- Chamas Tacos: locator https://chamas-tacos.com/restaurants lists Lille (search excerpt "Marseille Lille Bordeaux ...")
- Tasty Crousty: https://tastycrousty.io/ lists "Tasty Crousty - Toulouse, 31 Avenue de Muret, 31300 Toulouse"
- G La Dalle: NOT confirmed on the chain's own locator (https://g-ladalle.com/restaurants/ renders by script). Third-party evidence only:
  an Instagram page `g_la_dalle_lille` (248 rue des Postes, Lille) and an Apple Maps listing for Lille turned up in search.
- Tasty Crousty Lille: not on the chain site; Uber Eats lists "Tasty Crousty - Lille Poste" (third party).
The original ten-chain dataset preceded the additional Lille/Toulouse items documented below.

### Additional Lille/Toulouse chains (fetched 2026-10-15)

No per-item nutrition tables were published on the pages fetched, so the following dishes are marked `estimate: true` and are not in `fastfood.csv`:

| Chain | Dish | Menu evidence and locator | Estimation notes |
|---|---|---|---|
| Chick'n Beef | Classic Gourmet burger | https://chicknbeef.com/burger-gourmet/ lists the Classic Gourmet ingredients and Angus steak at 150 g; https://chicknbeef.com/burger-halal-lille/ lists three Lille addresses and halal certification. | The 150 g steak weight is published. Bun, cheddar, salad, onion and mayo weights are estimates; Ciqual raw beef steak and other ingredient matches are used. The mayo entry is a proxy for the mayo/BBQ sauce. |
| Chicken Street | Street Deal - Little Tenders | https://restaurants.chickenstreet.fr/burger-lille-flandres/ identifies the Lille Flandres branch and links the menu; its official post lists the Street Deal with Little Tenders, small fries and a 50 cl Cristaline Fraise (or another drink). https://restaurants.chickenstreet.fr/toulouse-jean-darc/ confirms Toulouse Jean d'Arc. | Tenders and fries weights are estimates; Ciqual breaded chicken nuggets and deep-fried fries are proxies. The 500 g drink estimate comes from the published 50 cl size and uses the Ciqual average for flavoured bottled water with sugar content unknown. |
| Black & White Burger | Black & Marquise Baguette | https://restaurants.bnwburger.com/toulouse-capitole/ and https://restaurants.bnwburger.com/lille-bethune/ are official restaurant pages; each describes its location as a halal restaurant. The Toulouse page's embedded official post describes the baguette, creamy smoked sauce, rocket, steaks, smoked cheddar, caramelised onions, and beef bacon or pastrami. | No portion weights are published. All gram amounts are estimates. Ciqual baguette, cooked minced beef, cheddar, cooked onion, rocket and mayonnaise are ingredient proxies; beef bacon/pastrami is omitted because Ciqual has no suitable halal-specific match, and mayonnaise represents the creamy sauce. |

The new YAML `checked_on` values record this source review date. No halal attribute or tag is stored in the catalogue data.

## French supermarket brands

The Open Food Facts supermarket export was downloaded on 2026-10-15. Its source, license,
coverage limits, hash, and refresh/import commands are documented in
[SUPERMARKET.md](SUPERMARKET.md). It does not change the Ciqual portions, restaurant
nutrition, or estimated dish datasets above.
