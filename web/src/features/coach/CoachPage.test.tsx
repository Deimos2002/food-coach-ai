import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { addWeeks, isoWeekOf, localDay } from '../../lib/dates'
import { CoachPage } from './CoachPage'
import { ready, sheet } from './fixtures'

const week = isoWeekOf(localDay())
const server = setupServer(
  http.get('*/week/:week', () => HttpResponse.json({ fact_sheet: sheet, complete_days: 3, cards: [] })),
  http.get('*/coach/weeks/:week', () => HttpResponse.json(ready)),
)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('shows current week review as cards', async () => {
  renderWithProviders(<CoachPage />)
  expect(screen.getByRole('heading', { name: 'Coach', level: 1 })).toBeInTheDocument()
  expect(screen.getByText(week)).toBeInTheDocument()
  const title = await screen.findByRole('heading', { name: ready.review.suggestions[0].title })
  expect(title.closest('article')).toHaveClass('ui-card')
  expect(screen.getByRole('button', { name: 'Helpful' })).toBeInTheDocument()
})

it('previous week arrow loads that week and next returns to current week', async () => {
  const previous = addWeeks(week, -1)
  server.use(http.get('*/week/:week', ({ params }) => HttpResponse.json({
    fact_sheet: sheet, complete_days: params.week === previous ? 2 : 3, cards: [],
  })))
  renderWithProviders(<CoachPage />)
  await screen.findByText(ready.review.suggestions[0].body)
  await userEvent.click(screen.getByRole('button', { name: 'Previous week' }))
  expect(await screen.findByText(/this week has 2 complete days/)).toBeInTheDocument()
  expect(screen.getByText(previous)).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'Weekly coach' })).queryByRole('button')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Next week' }))
  expect(await screen.findByText(ready.review.suggestions[0].body)).toBeInTheDocument()
})

it('shows week load errors and retries without offering generation', async () => {
  server.use(http.get('*/week/:week', () => HttpResponse.json({ detail: 'Week unavailable' }, { status: 503 })))
  renderWithProviders(<CoachPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Week unavailable')
  expect(screen.queryByRole('region', { name: 'Weekly coach' })).not.toBeInTheDocument()
  server.use(http.get('*/week/:week', () => HttpResponse.json({ fact_sheet: sheet, complete_days: 3, cards: [] })))
  await userEvent.click(screen.getByRole('button', { name: 'Retry loading week' }))
  expect(await screen.findByText(ready.review.suggestions[0].body)).toBeInTheDocument()
})

