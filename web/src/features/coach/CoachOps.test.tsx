import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { CoachOps } from './CoachOps'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('ops_panel_shows_budget_and_server_computed_metrics', async () => {
  server.use(http.get('*/coach/ops', ({ request }) => {
    expect(new URL(request.url).searchParams.get('month')).toBe('2026-10')
    return HttpResponse.json({ calls: 4, spent_eur: '0.125', cap_eur: '2',
      first_try_pass_rate: '0.75', fallback_rate: '0.25', p50_ms: 900, p95_ms: 1500 })
  }))
  renderWithProviders(<CoachOps month="2026-10" />)
  expect(await screen.findByText(/0,125 € of 2 €/)).toBeInTheDocument()
  expect(screen.getByText('4')).toBeInTheDocument()
  expect(screen.getByText('75 %')).toBeInTheDocument()
  expect(screen.getByText('25 %')).toBeInTheDocument()
  expect(screen.getByText('900 ms')).toBeInTheDocument()
  expect(screen.getByText(/1\s500 ms/)).toBeInTheDocument()
})

it('null_metrics_remain_unknown_and_known_zero_remains_zero', async () => {
  server.use(http.get('*/coach/ops', () => HttpResponse.json({
    calls: 0, spent_eur: '0', cap_eur: null, first_try_pass_rate: null,
    fallback_rate: null, p50_ms: null, p95_ms: null,
  })))
  renderWithProviders(<CoachOps month="2026-10" />)
  expect(await screen.findByText('0 € of ? €')).toBeInTheDocument()
  expect(screen.getAllByText('?')).toHaveLength(4)
  expect(screen.getByText('0')).toBeInTheDocument()
})

it('ops_error_is_visible', async () => {
  server.use(http.get('*/coach/ops', () => HttpResponse.json({ detail: 'Unavailable' }, { status: 503 })))
  renderWithProviders(<CoachOps month="2026-10" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Unavailable')
})
