# Coach stage 4 gate — frozen before the test run (2026-10-13)

- Prompt: `backend/prompts/coach/v4.md` (PROMPT_VERSION = "v4"), model `gpt-4.1-mini` (2025-04-14), temperature 0.3, max_tokens 800.
- Dev-split result that justified freezing: first-try pass 1.00 (21/21), hard failures 0, content-filtered 2 (injection fixtures, correct), €0.0011/review, p95 2.4 s.
- Gate thresholds (spec §4.4), fixed now, before the test split is run:

| Criterion | Threshold |
|---|---|
| Hard failures after validation | 0 |
| Validator catch rate (seeded bad outputs) | 100% |
| First-try pass rate | ≥ 90% |
| Mean rubric, LLM − template | ≥ 0.5 |
| Cost per review | ≤ €0.05 |
| Latency p95 | ≤ 15 s |

- Rubric: blind, merged LLM+template outputs, shuffled with a fixed seed; scored by the user 1–5 on specificity, actionability, goal fit, tone.
