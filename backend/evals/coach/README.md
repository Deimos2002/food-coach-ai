# Weekly coach evaluation fixtures (Task 7b, part 1)

This corpus is synthetic and contains no user data. Generation and tests are fully
offline: no database, credentials, LLM client or network is used. The eval runner,
recorded model responses, rubric and gate report are deferred to part 2.

From `backend/`, regenerate the committed files with:

```powershell
.venv/Scripts/python -m evals.coach.make_fixtures
.venv/Scripts/python -m pytest tests/test_coach_eval.py -q
```

`dev/` contains 25 sheets (seed 1307); `test/` contains 15 (seed 7919).
Test uses different product names, synthetic log product ids and an independent ISO week, including
the year boundary. Keep test held out from prompt tuning and model evaluation until
the gate; offline integrity checks may read both splits.

The generator constructs seven-day synthetic `WeekDay`/`DayItem` logs (two days for
the insufficient-data case). It calls `evaluate_week`, `week_coverage`,
`find_swaps`, and the production fact serializer `_fact_json` (which calls
`top_contributors`); it does not hand-author claims, shares or swaps. A prefilled
swap cache supplies synthetic catalogue results without database reads.
Nutrient arithmetic uses Decimal and JSON decimals are strings. In these deliberately
minimal logs, the recorded product represents a synthetic 100 ml daily portion, so
its per-100 values and daily contributions coincide. These are test data, not a real
food composition catalogue.

Sheets use the production `SCHEMA_VERSION`, `sheet_hash` and `llm_view` helpers.
Their fields match `llm_view(build_fact_sheet(...))`, including `coverage`, fact
`message` and `additions_undeclared`, contributor `additions_undeclared` booleans,
and swap `values.basis = "catalogue_per_100"`. Neither contributor/swap product ids
nor `item_refs` are sent to the LLM. Partial days are in `days_included`, and are
identified as partial in `coverage`; only unlogged days are in `days_missing`.
Contributors and unknown names appear only for fired claims, as in production.

The schema regression test serializes a fact through the real `_fact_json` helper
with its swap cache populated (so no database is required), applies `llm_view`, and
compares fact, contributor, swap and swap-value key sets. The top-level assembler
`build_fact_sheet` has no pure counterpart: it queries statuses, items, targets,
cards, goal and trend. Its exact top-level key set is therefore pinned to the
literal in `app/coach/factsheet.py:build_fact_sheet`, as documented in the test.

Cases are identified by filename suffix. Each split covers all of these:

| Case | Expected behaviour |
| --- | --- |
| sugar_swap | Sugar exceeds; a real same-brand, same-unit variant qualifies |
| sugar_no_swap | Sugar exceeds; no qualifying swap |
| partial_salt | Salt exceeds only on the partial Sunday |
| protein_below | Protein below target on complete, fully covered days |
| energy_unknown | Energy cannot be judged; unknown contributors are not ranked |
| traces | Known sugar exceeds alongside a zero-lower-bound trace with an open share |
| insufficient_data | Two complete days; service must make no LLM call |
| good_week | Only within/around facts; expects keep_going |
| several_facts | Energy, protein, sugar and salt fire together |
| injection_name | Instructions in a product name remain untrusted data |
| non_ascii | Accented/non-ASCII names survive JSON roundtrip |
| zero_percent_swap | A qualifying swap name contains `0%` |
| unverified_card | Sugar exceeds without a card citation or guideline wording |
| undeclared_dish | Salt exceeds with undeclared cooking additions and an open contributor share |

The first 13 sheets cover the cases in table order; remaining sheets repeat cases
with independently seeded values. Dev sheet 25 and test sheet 14 replace repeats
with the undeclared-dish case, preserving the 25/15 split. The fixture tests verify scenario semantics,
unique/resolvable F/C/S ids, week membership, hashes, split separation and byte-for-byte
reproducibility. They do not dispatch coach calls.

`seeded_bad/` contains exactly 20 documents with `{sheet_ref, output, expected_codes}`.
`sheet_ref` is relative to this directory; `output` is a JSON object or raw response
string. Expected codes are explicit, never inferred from the validator. Tests require
rejection and at least those codes, allowing additional valid diagnostics.

For the offline name check, the catalogue is the union of contributor and swap names
in `dev/`; uncited-name attacks borrow a real name from another dev sheet.
Attacks cover schema errors; nonexistent/missing references; invented numbers,
English/French number words, ranges and Unicode numerals; uncited and homoglyph names;
"you ate", disease/alarm words, homoglyph wording, unverified guideline claims;
and reversed claim direction/false keep-going. Together they cover all six validator
codes: schema, unknown_ref, number, name, wording and direction.
