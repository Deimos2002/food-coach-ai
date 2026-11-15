import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import type { MealView, SearchResult } from '../../lib/types'
import { MealCard } from '../day/MealCard'
import { AliasesPage } from './AliasesPage'

const server = setupServer(...handlers)
// Cast works around msw's deep-partial option typing; behaviour is msw's strict mode.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function meal(): MealView {
  return {
    meal_id: 7,
    meal_type: 'breakfast',
    skipped: false,
    items: [
      {
        item_id: 1,
        name: 'Oats',
        requested_product_id: 11,
        resolved_product_id: 11,
        profile_revision_id: 1,
        quantity: { kind: 'recorded', value: '40', lower: null, upper: null, unit: 'g', source: 'weighed' },
        nutrients: {},
        logged_at: '2026-10-13T08:00:00Z',
        consumed_at: null,
      },
    ],
  }
}

function renderMealCard() {
  return renderWithProviders(
    <MealCard mealType="breakfast" meal={meal()} day="2026-10-13" onAdd={() => {}} onSetSkipped={() => {}} />,
  )
}

describe('saved meals and aliases', () => {
  it('save_meal_from_items', async () => {
    let body: unknown = null
    server.use(
      http.post('*/saved-meals', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ saved_meal_id: 9 })
      }),
    )
    renderMealCard()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Meal options' }))
    await user.click(screen.getByRole('button', { name: 'Save as meal' }))
    await user.type(screen.getByLabelText('Name'), 'My breakfast')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(body).toEqual({
        name: 'My breakfast',
        items: [{ product_id: 11, amount: '40', unit: 'g' }],
      }),
    )
  })

  it('use_saved_meal_adds_and_shows_blocked_with_successors', async () => {
    const addCalls: { body: unknown; key: string | null }[] = []
    server.use(
      http.get('*/saved-meals', () =>
        HttpResponse.json([{ id: 3, name: 'Lunch combo', items: [{ product_id: 5, name: 'Rice', amount: '100', unit: 'g' }] }]),
      ),
      http.post('*/saved-meals/3/use', async ({ request }) => {
        const b = (await request.json()) as { meal_id: number }
        expect(b.meal_id).toBe(7)
        return HttpResponse.json({
          added: [101],
          blocked: [{ product_id: 5, reason: 'retired', successors: [21, 22] }],
        })
      }),
      http.post('*/meals/7/items', async ({ request }) => {
        addCalls.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') })
        return HttpResponse.json({ item_id: 200, nutrients: {} })
      }),
    )
    const { queryClient } = renderMealCard()
    const inv = vi.spyOn(queryClient, 'invalidateQueries')
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Meal options' }))
    await user.click(screen.getByRole('button', { name: 'Add saved meal' }))
    await user.click(await screen.findByRole('button', { name: 'Lunch combo' }))
    expect(await screen.findByText('Added 1 item')).toBeInTheDocument()
    await waitFor(() => expect(inv).toHaveBeenCalledWith({ queryKey: ['day', '2026-10-13'] }))
    await waitFor(() => expect(inv).toHaveBeenCalledWith({ queryKey: ['recent'] }))
    expect(await screen.findByText('Replaced — choose:')).toBeInTheDocument()
    const successorBtn = screen.getByRole('button', { name: 'Use product #22' })
    await user.click(successorBtn)
    // Picking a successor opens the add-item sheet pre-filled with the original amount and
    // unit; it must NOT add anything on its own.
    const sheet = await screen.findByRole('dialog', { name: 'Add item' })
    expect(within(sheet).getByLabelText('Amount')).toHaveValue('100')
    expect(within(sheet).getByLabelText('Unit')).toHaveValue('g')
    expect(addCalls).toHaveLength(0)
    await user.click(within(sheet).getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(addCalls).toHaveLength(1))
    expect(addCalls[0]?.body).toMatchObject({ product_id: 22, amount: '100', unit: 'g', quantity_source: 'user_estimate' })
    expect(addCalls[0]?.key).toBeTruthy()
  })

  it('shows no added-items message when a use adds nothing and blocks nothing', async () => {
    server.use(
      http.get('*/saved-meals', () =>
        HttpResponse.json([{ id: 3, name: 'Lunch combo', items: [{ product_id: 5, name: 'Rice', amount: '100', unit: 'g' }] }]),
      ),
      http.post('*/saved-meals/3/use', () => HttpResponse.json({ added: [], blocked: [] })),
    )
    renderMealCard()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Meal options' }))
    await user.click(screen.getByRole('button', { name: 'Add saved meal' }))
    await user.click(await screen.findByRole('button', { name: 'Lunch combo' }))
    await waitFor(() => expect(screen.queryByText(/Added \d/)).not.toBeInTheDocument())
    expect(screen.queryByText(/Already added/)).not.toBeInTheDocument()
  })

  it('tapping Use saved meal twice sends two different idempotency keys', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.get('*/saved-meals', () =>
        HttpResponse.json([{ id: 3, name: 'Lunch combo', items: [{ product_id: 5, name: 'Rice', amount: '100', unit: 'g' }] }]),
      ),
      http.post('*/saved-meals/3/use', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        return HttpResponse.json({ added: [101], blocked: [] })
      }),
    )
    renderMealCard()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Meal options' }))
    await user.click(screen.getByRole('button', { name: 'Add saved meal' }))
    await user.click(await screen.findByRole('button', { name: 'Lunch combo' }))
    await waitFor(() => expect(keys).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Lunch combo' }))
    await waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBeTruthy()
    expect(keys[0]).not.toBe(keys[1])
  })

  it('alias_create_uses_search_pick', async () => {
    const result: SearchResult = {
      product_id: 42,
      name: 'Cheddar cheese',
      brand: null,
      basis_unit: 'g',
      portions: [],
      kcal_per_100: '400',
    }
    let body: unknown = null
    server.use(
      http.get('*/search', () => HttpResponse.json([result])),
      http.get('*/recent-items', () => HttpResponse.json([])),
      http.post('*/aliases', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ alias_id: 1 })
      }),
    )
    renderWithProviders(<AliasesPage />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New alias' }))
    await user.type(screen.getByLabelText('Alias text'), 'cheddar')
    await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'chedd')
    await user.click(await screen.findByRole('button', { name: /Cheddar cheese/ }))
    await user.click(screen.getByRole('button', { name: 'Save alias' }))
    await waitFor(() => expect(body).toEqual({ text: 'cheddar', product_id: 42 }))
  })

  it('alias list shows suspended aliases', async () => {
    server.use(
      http.get('*/aliases', () =>
        HttpResponse.json([{ id: 1, text: 'cheddar', product_id: 42, suspended: true }]),
      ),
    )
    renderWithProviders(<AliasesPage />)
    expect(await screen.findByText('cheddar')).toBeInTheDocument()
    expect(screen.getByText('Suspended')).toBeInTheDocument()
  })
})
