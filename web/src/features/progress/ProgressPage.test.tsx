import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { emptyDay, server } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { ProgressPage } from './ProgressPage'

const weeks = new Set<string>()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => { cleanup(); server.resetHandlers(); weeks.clear() })
afterAll(() => server.close())
function setup() {
  server.use(http.get('*/week/:week', ({ params }) => {
    weeks.add(String(params.week))
    return HttpResponse.json({ complete_days: 0, cards: [], fact_sheet: { facts: [], days_missing: [], coverage: 'none' } })
  }))
}
it('week shows seven bars with today highlighted and unknown day bar shows ?', async () => {
  setup()
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  const bars = await screen.findAllByRole('figure')
  expect(bars).toHaveLength(7)
  expect(screen.getByLabelText('Calories 2026-10-15')).toHaveAttribute('aria-current', 'date')
  expect(within(bars[0]!).getByText('? kcal')).toBeInTheDocument()
})
it('month requests each spanned week including six weeks and ISO year boundaries', async () => {
  setup()
  renderWithProviders(<ProgressPage today="2027-01-15" />)
  await userEvent.click(screen.getByRole('button', { name: 'Month' }))
  await waitFor(() => expect([...weeks]).toEqual(expect.arrayContaining(['2026-W53', '2027-W01', '2027-W02', '2027-W03', '2027-W04'])))
  await userEvent.click(screen.getByRole('button', { name: 'Next month' }))
  await userEvent.click(screen.getByRole('button', { name: 'Next month' }))
  await userEvent.click(screen.getByRole('button', { name: 'Next month' }))
  await userEvent.click(screen.getByRole('button', { name: 'Next month' }))
  await waitFor(() => expect([...weeks]).toEqual(expect.arrayContaining(['2027-W17', '2027-W18', '2027-W19', '2027-W20', '2027-W21', '2027-W22'])))
})
it('protein days hit counts only known complete days', async () => {
  setup()
  server.use(http.get('*/targets', () => HttpResponse.json({ kcal: 2000, protein_g: 100, maintenance_kcal: 2200 })),
    http.get('*/days/:day', ({ params }) => {
      const d = emptyDay(String(params.day))
      if (d.day === '2026-10-12') { d.status = 'complete'; d.nutrients.protein.recorded = { lower: '120', upper: '120' } }
      return HttpResponse.json(d)
    }))
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  expect(await screen.findByText('1 of 1 known days hit target; 6 unknown')).toBeInTheDocument()
})
it('log weight inline saves and updates trend', async () => {
  setup()
  let saved = false
  server.use(http.put('*/weights/:day', () => { saved = true; return HttpResponse.json({ kg: '72.5' }) }),
    http.get('*/weights/trend', () => HttpResponse.json(saved ? [{ day: '2026-10-15', trend: '72.5', flagged: false, suggest_confirm: false }] : [])))
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  await userEvent.type(screen.getByLabelText("Today's weight (kg)"), '72.5')
  await userEvent.click(screen.getByRole('button', { name: 'Log weight' }))
  expect(await screen.findByText('Weight saved.')).toBeInTheDocument()
  expect(await screen.findByText(/2026-10-15 .* Trend:/)).toBeInTheDocument()
})

it('week arrows change the data period and known overtarget bars retain real values', async () => {
  setup()
  server.use(http.get('*/targets', () => HttpResponse.json({ kcal: 2000, protein_g: 100, maintenance_kcal: 2200 })),
    http.get('*/days/:day', ({ params }) => {
      const d = emptyDay(String(params.day)); d.status = 'complete'
      d.nutrients.energy_kcal.recorded = { lower: '2400', upper: '2400' }
      return HttpResponse.json(d)
    }))
  renderWithProviders(<ProgressPage today="2026-10-15" />)
  await waitFor(() => expect(screen.getAllByText('2 400 kcal')).toHaveLength(7))
  expect(screen.getByText('Average: 2 400 kcal / day')).toBeInTheDocument()
  expect(screen.getByLabelText('Calories vs target 2026-10-15')).toHaveAttribute('value', '2000')
  await userEvent.click(screen.getByRole('button', { name: 'Previous week' }))
  await waitFor(() => expect(weeks.has('2026-W41')).toBe(true))
  expect(screen.getByLabelText('Calories 2026-10-05')).toBeInTheDocument()
})
