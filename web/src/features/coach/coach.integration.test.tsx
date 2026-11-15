import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { SettingsPage } from '../settings/SettingsPage'
import { WeekPage } from '../week/WeekPage'
import { ready, sheet } from './fixtures'

const server = setupServer(
  http.get('*/coach/weeks/2026-W42', () => HttpResponse.json(ready)),
  http.get('*/week/2026-W42', () => HttpResponse.json({ fact_sheet: sheet, complete_days: 3, cards: [] })),
  http.get('*/coach/ops', () => HttpResponse.json({
    calls: 0, spent_eur: '0', cap_eur: '2', first_try_pass_rate: null,
    fallback_rate: null, p50_ms: null, p95_ms: null,
  })),
)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('week_page_renders_coach_card', async () => {
  renderWithProviders(<Routes>
    <Route path="/week/:isoWeek" element={<WeekPage />} />
  </Routes>, { route: '/week/2026-W42' })
  const card = await screen.findByRole('region', { name: 'Weekly coach' })
  expect(await within(card).findByText(ready.review.suggestions[0].body)).toBeInTheDocument()
})

it('settings_renders_coach_ops', async () => {
  renderWithProviders(<SettingsPage />)
  const panel = screen.getByRole('region', { name: 'Coach ops' })
  expect(await within(panel).findByText('0 € of 2 €')).toBeInTheDocument()
})
