import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { handlers, emptyDay } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import type { ItemView } from '../../lib/types'
import { EditItemDialog } from './EditItemDialog'
import { ItemRow } from './ItemRow'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const DAY = '2026-10-13'

function baseItem(overrides: Partial<ItemView> = {}): ItemView {
  return {
    item_id: 50,
    name: 'Greek yogurt',
    requested_product_id: 11,
    resolved_product_id: 11,
    profile_revision_id: 1,
    quantity: {
      kind: 'recorded',
      value: '200',
      lower: null,
      upper: null,
      unit: 'g',
      source: 'user_estimate',
    },
    nutrients: {},
    logged_at: '2026-10-13T08:00:00Z',
    consumed_at: null,
    ...overrides,
  }
}

function serveDay(day: string, status: 'not_logged' | 'partial' | 'complete' = 'not_logged', meals = []) {
  server.use(http.get(`*/days/${day}`, () => HttpResponse.json({ ...emptyDay(day), status, meals })))
}

describe('EditItemDialog', () => {
  it('patch_sends_only_changed_fields', async () => {
    serveDay(DAY)
    const item = baseItem()
    let captured: Record<string, unknown> | null = null
    server.use(
      http.patch(`*/meal-items/${item.item_id}`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ item_id: item.item_id, nutrients: {} })
      }),
    )
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={vi.fn()} />)
    const user = userEvent.setup()
    const amount = screen.getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '210')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(captured).toEqual({ amount: '210' }))
  })

  it('amount_normalised_for_diff_sends_nothing', async () => {
    serveDay(DAY)
    const item = baseItem()
    let called = false
    server.use(
      http.patch(`*/meal-items/${item.item_id}`, async () => {
        called = true
        return HttpResponse.json({ item_id: item.item_id, nutrients: {} })
      }),
    )
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={vi.fn()} />)
    const user = userEvent.setup()
    const amount = screen.getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '200.0')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await new Promise((r) => setTimeout(r, 50))
    expect(called).toBe(false)
  })

  it('closes_after_a_successful_save', async () => {
    serveDay(DAY)
    const item = baseItem()
    server.use(
      http.patch(`*/meal-items/${item.item_id}`, () => HttpResponse.json({ item_id: item.item_id, nutrients: {} })),
    )
    const onClose = vi.fn()
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={onClose} />)
    const user = userEvent.setup()
    const amount = screen.getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '210')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('make_exact_sends_null_bounds', async () => {
    serveDay(DAY)
    const item = baseItem({
      quantity: { kind: 'estimated_range', value: '200', lower: '180', upper: '220', unit: 'g', source: 'user_estimate' },
    })
    let captured: Record<string, unknown> | null = null
    server.use(
      http.patch(`*/meal-items/${item.item_id}`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ item_id: item.item_id, nutrients: {} })
      }),
    )
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Make exact' }))
    await waitFor(() => expect(captured).toEqual({ lower: null, upper: null }))
  })

  it('make_exact_sends_the_edited_amount_when_changed', async () => {
    serveDay(DAY)
    const item = baseItem({
      quantity: { kind: 'estimated_range', value: '200', lower: '180', upper: '220', unit: 'g', source: 'user_estimate' },
    })
    let captured: Record<string, unknown> | null = null
    server.use(
      http.patch(`*/meal-items/${item.item_id}`, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ item_id: item.item_id, nutrients: {} })
      }),
    )
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={vi.fn()} />)
    const user = userEvent.setup()
    const amount = screen.getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '210')
    await user.click(screen.getByRole('button', { name: 'Make exact' }))
    await waitFor(() => expect(captured).toEqual({ amount: '210', lower: null, upper: null }))
  })

  it('delete_on_complete_day_warns_partial', async () => {
    serveDay(DAY, 'complete')
    const item = baseItem()
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={vi.fn()} />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText(/The day will be marked partial/)).toBeInTheDocument()
  })

  it('move_to_other_day_creates_meal_first', async () => {
    serveDay(DAY)
    const item = baseItem()
    const otherDay = '2026-10-14'
    server.use(http.get(`*/days/${otherDay}`, () => HttpResponse.json({ ...emptyDay(otherDay), meals: [] })))
    let createdMealBody: Record<string, unknown> | null = null
    let movedTo: number | null = null
    server.use(
      http.post('*/meals', async ({ request }) => {
        createdMealBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ meal_id: 999 })
      }),
      http.post(`*/meal-items/${item.item_id}/move`, async ({ request }) => {
        const body = (await request.json()) as { meal_id: number }
        movedTo = body.meal_id
        return HttpResponse.json({ item_id: item.item_id, meal_id: body.meal_id })
      }),
    )
    const onClose = vi.fn()
    renderWithProviders(<EditItemDialog item={item} day={DAY} onClose={onClose} />)
    const user = userEvent.setup()
    await user.clear(screen.getByLabelText('Date'))
    await user.type(screen.getByLabelText('Date'), otherDay)
    await user.selectOptions(screen.getByLabelText('Meal'), 'dinner')
    await user.click(screen.getByRole('button', { name: 'Move' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(createdMealBody).toEqual({ day: otherDay, meal_type: 'dinner' })
    expect(movedTo).toBe(999)
  })

  it('merged_badge_when_requested_differs', () => {
    const item = baseItem({ requested_product_id: 11, resolved_product_id: 12 })
    renderWithProviders(
      <ul>
        <ItemRow item={item} />
      </ul>,
    )
    expect(screen.getByText('product since merged / updated')).toBeInTheDocument()
  })

  it('no badge when requested and resolved match', () => {
    const item = baseItem({ requested_product_id: 11, resolved_product_id: 11 })
    renderWithProviders(
      <ul>
        <ItemRow item={item} />
      </ul>,
    )
    expect(screen.queryByText('product since merged / updated')).not.toBeInTheDocument()
  })
})
