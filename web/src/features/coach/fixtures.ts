import type { FactSheet } from '../../lib/types'

export const sheet: FactSheet = {
  schema_version: 1, week: '2026-W42', coverage: '3 complete days; remaining days not logged.',
  days_included: ['2026-10-12', '2026-10-13', '2026-10-14'],
  days_missing: ['2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18'],
  goal: null, trend: null, hash: 'fixture-hash',
  facts: [{
    id: 'F1', rule: 'sugars_daily', claim: 'exceeds',
    values: { recorded_lower: '86', threshold: '50', unit: 'g' },
    days: ['2026-10-14'], card_id: 'who_free_sugars',
    message: 'Your recorded sugars reached at least 86 g on Wed 14 Oct.',
    contributors: [{ id: 'C1', name: 'Peach Drink', share: ['1.00', '1.00'],
      additions_undeclared: false, item_refs: [] }], unknown_for: [], additions_undeclared: [],
    swaps: [{ id: 'S1', for: 'C1', name: 'Peach Drink Sugarfree',
      values: { from_lower: '11', to_upper: '0', unit: 'g/100ml', basis: 'catalogue_per_100' } }],
  }],
}

export const ready = {
  status: 'ok' as const,
  review: {
    id: 42, iso_week: '2026-W42', generation_no: 1,
    fact_sheet: sheet, source: 'llm' as const, fallback_reason: null,
    prompt_version: 'v1', created_at: '2026-10-18T12:00:00Z',
    suggestions: [{ kind: 'swap' as const, title: 'Try the sugarfree variant',
      body: 'Consider Peach Drink Sugarfree for your next recorded drink.',
      fact_ids: ['F1'], swap_ids: ['S1'] }] as const,
  },
}
