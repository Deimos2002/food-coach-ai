// Shared MSW fixtures and default handlers. Shapes follow real dev-backend responses (see types.ts).
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { DayView, Nutrient, NutrientState } from '../lib/types'

export const NUTRIENTS: Nutrient[] = [
  'energy_kcal',
  'protein',
  'carbohydrate',
  'sugars',
  'fat',
  'saturated_fat',
  'fibre',
  'salt',
  'caffeine',
  'alcohol',
  'polyols',
  'erythritol',
]

export function emptyNutrientState(): NutrientState {
  return {
    recorded: { lower: '0', upper: '0' },
    unresolved: [],
    unquantified: [],
    additions_undeclared: [],
    fields_complete: true,
    log_complete: false,
  }
}

export function emptyDay(day: string): DayView {
  return {
    day,
    status: 'not_logged',
    completed_at: null,
    meals: [],
    nutrients: Object.fromEntries(NUTRIENTS.map((n) => [n, emptyNutrientState()])) as DayView['nutrients'],
  }
}

export const handlers = [
  http.get('*/targets', () => HttpResponse.json({ detail: 'no profile set' }, { status: 404 })),
  http.get('*/days/:day', ({ params }) => HttpResponse.json(emptyDay(String(params.day)))),
  http.get('*/recent-items', () => HttpResponse.json([])),
  http.get('*/saved-meals', () => HttpResponse.json([])),
  http.get('*/aliases', () => HttpResponse.json([])),
  http.get('*/search', () => HttpResponse.json([])),
  http.get('*/weights/trend', () => HttpResponse.json([])),
  http.get('*/profile', () => HttpResponse.json({ detail: 'no profile set' }, { status: 404 })),
  // Week and Settings now mount coach readers. Keep shared fixtures explicit;
  // coach mutations still require a test-specific handler under strict MSW.
  http.get('*/coach/weeks/:week', () => HttpResponse.json({ detail: 'no review for this week' }, { status: 404 })),
  http.get('*/coach/ops', () => HttpResponse.json({
    calls: 0, spent_eur: '0', cap_eur: '2', first_try_pass_rate: null,
    fallback_rate: null, p50_ms: null, p95_ms: null,
  })),
]

export const server = setupServer(...handlers)
