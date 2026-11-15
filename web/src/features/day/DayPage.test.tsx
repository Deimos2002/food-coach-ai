import { AddPage } from '../add/AddPage'
import type { PickedProduct } from '../add/ProductSearch'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation, Route, Routes } from 'react-router-dom'
import { emptyDay, handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { setTokenProvider } from '../../lib/auth'
import type { DayView, Product } from '../../lib/types'
import { DayPage } from './DayPage'

const server = setupServer(...handlers)
// Cast works around msw's deep-partial option typing; behaviour is msw's strict mode.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const DAY = '2026-10-13'

function renderDay(onAdd = vi.fn()) {
  renderWithProviders(
    <Routes>
      <Route path="/day/:day" element={<DayPage onAdd={onAdd} />} />
    </Routes>,
    { route: `/day/${DAY}` },
  )
  return onAdd
}

function loggedDay(): DayView {
  const d = emptyDay(DAY)
  d.status = 'partial'
  d.meals = [
    {
      meal_id: 7,
      meal_type: 'breakfast',
      skipped: false,
      items: [
        {
          item_id: 1,
          name: 'Oats',
          requested_product_id: 1,
          resolved_product_id: 1,
          profile_revision_id: 1,
          quantity: { kind: 'recorded', value: '40', lower: null, upper: null, unit: 'g', source: 'weighed' },
          nutrients: { energy_kcal: { known: true, lower: '150', upper: '150', qualifier: 'none' } },
          logged_at: '2026-10-13T08:00:00Z',
          consumed_at: null,
        },
      ],
    },
  ]
  return d
}

describe('DayPage', () => {
  it('shows meals, items and status', async () => {
    server.use(http.get('*/days/:day', () => HttpResponse.json(loggedDay())))
    renderDay()
    expect(await screen.findByText('Oats')).toBeInTheDocument()
    expect(screen.getByText('40 g')).toBeInTheDocument()
    expect(screen.getByText('150 kcal')).toBeInTheDocument()
    expect(screen.getByText('Partial')).toBeInTheDocument()
    for (const m of ['Breakfast', 'Lunch', 'Dinner', 'Snack']) {
      expect(screen.getByRole('heading', { name: m })).toBeInTheDocument()
    }
  })

  it('never_says_you_ate', async () => {
    server.use(http.get('*/days/:day', () => HttpResponse.json(loggedDay())))
    renderDay()
    await screen.findByText('Oats')
    expect(document.body.textContent ?? '').not.toMatch(/you ate/i)
  })

  it('mark_day_complete_calls_api', async () => {
    let body: unknown = null
    let complete = false
    server.use(
      http.get('*/days/:day', () => {
        const d = loggedDay()
        if (complete) d.status = 'complete'
        return HttpResponse.json(d)
      }),
      http.put('*/days/:day/status', async ({ request }) => {
        body = await request.json()
        complete = true
        return HttpResponse.json({ day: DAY, status: 'complete' })
      }),
    )
    renderDay()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Mark day complete' }))
    await waitFor(() => expect(body).toEqual({ status: 'complete' }))
    expect(await screen.findByRole('button', { name: 'Reopen' })).toBeInTheDocument()
    expect(screen.getByText('Complete ✓')).toBeInTheDocument()
  })

  it('add_on_missing_meal_creates_meal_then_calls_onAdd', async () => {
    let body: unknown = null
    server.use(
      http.post('*/meals', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ meal_id: 42 })
      }),
    )
    const onAdd = renderDay()
    const user = userEvent.setup()
    const lunch = await screen.findByTestId('meal-lunch')
    await user.click(within(lunch).getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith(42))
    expect(body).toEqual({ day: DAY, meal_type: 'lunch' })
  })

  it('mark skipped calls setSkipped', async () => {
    let body: unknown = null
    server.use(
      http.get('*/days/:day', () => HttpResponse.json(loggedDay())),
      http.put('*/meals/7/skipped', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ meal_id: 7, skipped: true })
      }),
    )
    renderDay()
    const user = userEvent.setup()
    await screen.findByText('Oats')
    await user.click(within(screen.getByTestId('meal-breakfast')).getByRole('button', { name: 'Mark skipped' }))
    await waitFor(() => expect(body).toEqual({ skipped: true }))
  })

  it('tapping an item opens the edit dialog', async () => {
    server.use(http.get('*/days/:day', () => HttpResponse.json(loggedDay())))
    renderDay()
    const user = userEvent.setup()
    await screen.findByText('Oats')
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(await screen.findByRole('dialog', { name: 'Edit item' })).toBeInTheDocument()
  })

  it('reopens the add-item sheet pre-selected on the new product after returning from create', async () => {
    server.use(
      http.get('*/days/:day', () => HttpResponse.json(loggedDay())),
      http.get('*/products/55', () =>
        HttpResponse.json({
          product_id: 55,
          owner: 'user',
          name: 'Homemade bread',
          brand: null,
          basis_unit: 'g',
          kind: 'product',
          includes_cooking_additions: null,
          values: {} as Product['values'],
          portions: [],
          revisions: [],
          pending_reviews: [],
          overrides: [],
        } satisfies Product),
      ),
    )
    setTokenProvider({ getToken: async () => 'test-token' })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={[{ pathname: `/day/${DAY}`, state: { reopenMealId: 7, productId: 55 } }]}
        >
          <Routes>
            <Route path="/day/:day" element={<DayPage />} />
            <Route path="/add/:day/:meal" element={<ReturnedAdd />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('main', { name: 'Add food' })).toBeInTheDocument()
    expect(await screen.findByText('Homemade bread')).toBeInTheDocument()
  })

  it('prev and next move one day', async () => {
    renderDay()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Previous day' }))
    expect(await screen.findByRole('button', { name: 'Choose date 2026-10-12' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next day' }))
    await user.click(screen.getByRole('button', { name: 'Next day' }))
    expect(await screen.findByRole('button', { name: 'Choose date 2026-10-14' })).toBeInTheDocument()
  })

  it('rolls over to the new local day at midnight when viewing "/"', async () => {
    try {
      vi.useFakeTimers()
      vi.setSystemTime(new Date(2026, 9, 13, 23, 59, 0))
      renderWithProviders(
        <Routes>
          <Route path="/" element={<DayPage onAdd={vi.fn()} />} />
        </Routes>,
        { route: '/' },
      )
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(screen.getByRole('button', { name: 'Choose date 2026-10-13' })).toBeInTheDocument()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2 * 60 * 1000)
      })
      expect(screen.getByRole('button', { name: 'Choose date 2026-10-14' })).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not act on a stale meal id when the day rolled over before the click', async () => {
    try {
      const onAdd = vi.fn()
      vi.useFakeTimers()
      vi.setSystemTime(new Date(2026, 9, 13, 12, 0, 0))
      server.use(http.get('*/days/:day', () => HttpResponse.json(loggedDay())))
      renderWithProviders(
        <Routes>
          <Route path="/" element={<DayPage onAdd={onAdd} />} />
        </Routes>,
        { route: '/' },
      )
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50)
      })
      expect(screen.getByRole('button', { name: 'Choose date 2026-10-13' })).toBeInTheDocument()
      expect(screen.getByTestId('meal-breakfast')).toBeInTheDocument()
      // Clock skips to the next day without the scheduled midnight timer having fired yet,
      // simulating the moment right after midnight before the tab has re-rendered.
      vi.setSystemTime(new Date(2026, 9, 14, 0, 0, 5))
      fireEvent.click(within(screen.getByTestId('meal-breakfast')).getByRole('button', { name: 'Add' }))
      expect(onAdd).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: 'Choose date 2026-10-14' })).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})

it('Today opens full-page Add after creating a missing meal', async () => {
  server.use(http.post('*/meals', () => HttpResponse.json({ meal_id: 42 })))
  renderWithProviders(<Routes><Route path="/day/:day" element={<DayPage />} /><Route path="/add/:day/:meal" element={<h1>Add for 2026-10-13 meal 42</h1>} /></Routes>, { route: `/day/${DAY}` })
  await userEvent.setup().click(within(await screen.findByTestId('meal-lunch')).getByRole('button', { name: 'Add' }))
  expect(await screen.findByRole('heading', { name: 'Add for 2026-10-13 meal 42' })).toBeInTheDocument()
})

function ReturnedAdd() {
  const location = useLocation()
  const state = location.state as { initialProduct: PickedProduct }
  return <AddPage mealId={7} day={DAY} initialProduct={state.initialProduct} onClose={() => {}} />
}
