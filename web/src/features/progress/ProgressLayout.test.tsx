import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { ProgressPage } from './ProgressPage'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('keeps the weight chart visible and reveals entry actions only when history is opened', async () => {
  server.use(
    http.get('*/week/:week', () => HttpResponse.json({ complete_days: 0, cards: [], fact_sheet: { facts: [], days_missing: [], coverage: 'none' } })),
    http.get('*/weights/trend', () => HttpResponse.json([
      { day: '2026-10-14', trend: '72.5', flagged: false, suggest_confirm: false },
      { day: '2026-10-15', trend: '72.4', flagged: false, suggest_confirm: false },
    ])),
  )
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  expect(await screen.findByRole('img', { name: 'Weight trend over the last 30 entries' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Edit weight 2026-10-15' })).not.toBeVisible()
  await userEvent.click(screen.getByText('Weight history (2 entries)'))
  expect(screen.getByRole('button', { name: 'Edit weight 2026-10-15' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Delete weight 2026-10-15' })).toBeVisible()
})

it('keeps daily targets available behind chart details instead of crowding each bar', async () => {
  server.use(
    http.get('*/week/:week', () => HttpResponse.json({ complete_days: 0, cards: [], fact_sheet: { facts: [], days_missing: [], coverage: 'none' } })),
    http.get('*/targets', () => HttpResponse.json({ kcal: 2100, protein_g: 120, maintenance_kcal: 2400 })),
  )
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  const details = screen.getByText('Targets & chart details')
  expect(await screen.findAllByText('2100 kcal')).toHaveLength(7)
  expect(screen.getAllByText('2100 kcal')[0]).not.toBeVisible()
  await userEvent.click(details)
  expect(screen.getAllByText('2100 kcal')[0]).toBeVisible()
})

it('opens weight history automatically when an entry needs confirmation', async () => {
  server.use(
    http.get('*/week/:week', () => HttpResponse.json({ complete_days: 0, cards: [], fact_sheet: { facts: [], days_missing: [], coverage: 'none' } })),
    http.get('*/weights/trend', () => HttpResponse.json([
      { day: '2026-10-15', trend: '72.4', flagged: true, suggest_confirm: true },
    ])),
  )
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  expect(await screen.findByRole('button', { name: 'Confirm real' })).toBeVisible()
})
