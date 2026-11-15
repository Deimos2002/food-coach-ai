import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { localDay } from '../../lib/dates'
import { WeightPage } from './WeightPage'
import { TrendList } from './TrendList'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => { cleanup(); server.resetHandlers() })
afterAll(() => server.close())

it('second_weight_same_day_asks_replace_then_retries', async () => {
  const requests: { replace: string | null; body: unknown }[] = []
  server.use(http.put('*/weights/:day', async ({ request, params }) => {
    expect(params.day).toBe(localDay())
    const replace = new URL(request.url).searchParams.get('replace')
    requests.push({ replace, body: await request.json() })
    return replace === 'true' ? HttpResponse.json({ day: params.day, kg: '72.5' })
      : HttpResponse.json({ detail: 'weight exists' }, { status: 409 })
  }))
  renderWithProviders(<WeightPage />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("Today's weight (kg)"), '72,5')
  await user.click(screen.getByRole('button', { name: 'Log weight' }))
  expect(await screen.findByText("Replace today's weight?")).toBeInTheDocument()
  expect(requests).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Replace' }))
  await waitFor(() => expect(requests).toEqual([
    { replace: 'false', body: { kg: '72.5' } }, { replace: 'true', body: { kg: '72.5' } },
  ]))
})

it('outlier_shows_confirm', async () => {
  let confirmed = false
  server.use(
    http.get('*/weights/trend', () => HttpResponse.json([{ day: '2026-10-12', trend: '72.00', flagged: true, suggest_confirm: true }])),
    http.post('*/weights/2026-10-12/confirm', () => { confirmed = true; return HttpResponse.json({ day: '2026-10-12', confirmed: true }) }),
  )
  renderWithProviders(<WeightPage />)
  expect(await screen.findByText('Outlier — excluded')).toBeInTheDocument()
  expect(screen.getByText('Real change?')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Confirm real' }))
  await waitFor(() => expect(confirmed).toBe(true))
})

it('entry_edit_replaces_explicitly_and_delete_waits_for_confirmation', async () => {
  let edited: unknown
  let deleted = false
  server.use(
    http.put('*/weights/2026-10-12', async ({ request }) => {
      expect(new URL(request.url).searchParams.get('replace')).toBe('true')
      edited = await request.json()
      return HttpResponse.json({ day: '2026-10-12', kg: '72.5' })
    }),
    http.delete('*/weights/2026-10-12', () => { deleted = true; return new HttpResponse(null, { status: 204 }) }),
  )
  const { queryClient } = renderWithProviders(<TrendList points={[{ day: '2026-10-12', trend: '72.00', flagged: false, suggest_confirm: false }]} />)
  queryClient.setQueryData(['targets', '2026-10-13'], {})
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Edit weight 2026-10-12' }))
  const input = screen.getByLabelText('Replacement weight for 2026-10-12 (kg)')
  expect(input).toHaveValue('')
  await user.type(input, '72,5')
  await user.click(screen.getByRole('button', { name: 'Save replacement' }))
  await waitFor(() => expect(edited).toEqual({ kg: '72.5' }))
  await waitFor(() => expect(queryClient.getQueryState(['targets', '2026-10-13'])?.isInvalidated).toBe(true))
  await user.click(screen.getByRole('button', { name: 'Delete weight 2026-10-12' }))
  expect(deleted).toBe(false)
  await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
  await waitFor(() => expect(deleted).toBe(true))
})

it('only_last_thirty_entries_are_listed_with_a_display_sparkline', () => {
  const points = Array.from({ length: 31 }, (_, i) => ({
    day: i === 0 ? '2026-09-30' : `2026-10-${String(i).padStart(2, '0')}`,
    trend: '72.00', flagged: false, suggest_confirm: false,
  }))
  renderWithProviders(<TrendList points={points} />)
  expect(screen.getAllByRole('listitem')).toHaveLength(30)
  expect(screen.queryByText(/2026-09-30/)).not.toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Weight trend over the last 30 entries' })).toBeInTheDocument()
  const coordinates = document.querySelector('polyline')?.getAttribute('points')
  expect(coordinates).not.toMatch(/NaN|Infinity/)
})
