# Weekly coach LLM gate (spec §4.4)

| Criterion | Threshold | Actual | Result |
|---|---|---|---|
| Hard failures after validation | 0 | 0 | PASS |
| Validator catch rate (seeded bad outputs) | 1.0 | 1.00 | PASS |
| First-try pass rate | 0.90 | 1.00 | PASS |
| Mean rubric, LLM minus template | 0.5 | 1.08 | PASS |
| Cost per review | 0.05 | 0.0012 | PASS |
| Latency p95 | 15000 | 2366 | PASS |

**Decision: PASS** -- ships source=llm.

## Rubric method (deviation from spec §4.3)

- Spec §4.3 has the user score the rubric. Here two AI raters (Claude, Codex) scored independently from a blind packet (`docs/evals/rubric/packet.md`: 27 items, the fact sheet plus the shown review, source hidden, order = `Random(20261013)` as in `run_blind_rubric`). The user then settled the disagreements.
- Blindness is partial: the template's fixed wording ("over 7 day(s)") is recognisable.
- Agreement before alignment: 57/108 exact (53%), 97/108 within one point (90%). Eleven scores differed by 2 or more. Ten were goal_fit on template items (Claude had been docking goal_fit for having no action) and one was specificity on item 5 (an LLM review called an item the main source while its share was open). The user accepted the proposed resolutions. All other scores are the mean of the two raters, rounded half-up.
- Sensitivity: rubric delta 1.30 with Claude's scores alone, 0.89 with Codex's alone, 1.08 with the consensus. The criterion passes under every scoring.
- Files: `docs/evals/rubric/scores_claude.csv`, `scores_codex.csv`, `scores_consensus.csv`.
- Prompt v5 candidates seen during scoring (not applied; the gate stays frozen): don't call an item the main source when its share is open; mention undeclared cooking additions.
