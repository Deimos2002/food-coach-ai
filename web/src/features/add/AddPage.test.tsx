import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import type { SearchResult } from '../../lib/types'
import { _resetForTests, stats } from '../../lib/trial'
import { AddPage } from './AddPage'

const server = setupServer(...handlers)
// Cast works around msw's deep-partial option typing; behaviour is msw's strict mode.
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
beforeEach(() => {
  localStorage.clear()
  _resetForTests()
})

const DAY = '2026-10-13'
const MEAL = 7

const yogurt: SearchResult = {
  product_id: 11,
  name: 'Greek yogurt',
  brand: 'Fage',
  basis_unit: 'g',
  portions: [{ label: '1 pot', amount: '170', kind: 'label' }],
  kcal_per_100: '97',
}

type Captured = { body: Record<string, unknown>; key: string | null }

function serveSearch(results: SearchResult[] = [yogurt]) {
  server.use(http.get('*/search', () => HttpResponse.json(results)))
}

function captureAdds(respond?: (n: number) => Response) {
  const calls: Captured[] = []
  server.use(
    http.post(`*/meals/${MEAL}/items`, async ({ request }) => {
      calls.push({
        body: (await request.json()) as Record<string, unknown>,
        key: request.headers.get('Idempotency-Key'),
      })
      return respond ? respond(calls.length) : HttpResponse.json({ item_id: 100, nutrients: {} })
    }),
  )
  return calls
}

function renderSheet(onClose = vi.fn()) {
  const r = renderWithProviders(<AddPage mealId={MEAL} day={DAY} onAdded={onClose} onClose={() => {}} />)
  return { onClose, ...r }
}

async function pickYogurt(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'yog')
  await user.click(await screen.findByRole('button', { name: /^Greek yogurt/ }))
}

describe('AddPage', () => {
  it('double_tap_add_sends_one_request', async () => {
    serveSearch()
    const calls = captureAdds()
    const { onClose, queryClient } = renderSheet()
    const inv = vi.spyOn(queryClient, 'invalidateQueries')
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.dblClick(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(calls).toHaveLength(1)
    expect(calls[0]?.body).toEqual({ product_id: 11, amount: '150', unit: 'g', quantity_source: 'user_estimate' })
    expect(inv).toHaveBeenCalledWith({ queryKey: ['day', DAY] })
    expect(inv).toHaveBeenCalledWith({ queryKey: ['recent'] })
    expect(stats().cold.n).toBe(1)
  })

  it('network_retry_reuses_key', async () => {
    serveSearch()
    const calls = captureAdds((n) =>
      n === 1 ? HttpResponse.error() : HttpResponse.json({ item_id: 100, nutrients: {} }),
    )
    const { onClose } = renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 3000 })
    expect(calls).toHaveLength(2)
    expect(calls[0]?.key).toBeTruthy()
    expect(calls[1]?.key).toBe(calls[0]?.key)
  })

  it('new quantity after a failure gets a new key', async () => {
    serveSearch()
    const calls = captureAdds(() => HttpResponse.json({ detail: 'amount too large' }, { status: 422 }))
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '9000')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('amount too large')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Amount'))
    await user.type(screen.getByLabelText('Amount'), '90')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1]?.key).not.toBe(calls[0]?.key)
  })

  it('comma_amount_sent_as_dot_string', async () => {
    serveSearch()
    const calls = captureAdds()
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    const amount = screen.getByLabelText('Amount')
    expect(amount).toHaveAttribute('inputmode', 'decimal')
    await user.type(amount, '12,5')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.body.amount).toBe('12.5')
  })

  it('portion_chip_sets_amount_and_source', async () => {
    serveSearch()
    const calls = captureAdds()
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.click(screen.getByRole('button', { name: '1 pot (170 g)' }))
    expect(screen.getByLabelText('Amount')).toHaveValue('170')
    expect(screen.getByLabelText('Source')).toHaveValue('label_portion')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.body).toMatchObject({ amount: '170', quantity_source: 'label_portion' })
  })

  it('estimate range sends lower and upper', async () => {
    serveSearch()
    const calls = captureAdds()
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '100')
    await user.click(screen.getByLabelText('Estimate range'))
    await user.type(screen.getByLabelText('Lower'), '80')
    await user.type(screen.getByLabelText('Upper'), '120,5')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.body).toMatchObject({ amount: '100', lower: '80', upper: '120.5' })
  })

  it('unknown_kcal_shows_question_mark', async () => {
    serveSearch([{ ...yogurt, kcal_per_100: null }])
    renderSheet()
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'yog')
    expect(await screen.findByText('? kcal / 100 g')).toBeInTheDocument()
    expect(screen.queryByText(/^0 kcal/)).not.toBeInTheDocument()
  })

  it('shows kcal per 100 and a create-product link', async () => {
    serveSearch()
    renderSheet()
    const user = userEvent.setup()
    await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'yog')
    expect(await screen.findByText('97 kcal / 100 g')).toBeInTheDocument()
    expect(screen.getByText('Fage')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Not found? Create product' })).toHaveAttribute(
      'href',
      `/products/new?meal=${MEAL}&day=${DAY}`,
    )
  })

  it('retired_product_shows_successors', async () => {
    serveSearch()
    const calls = captureAdds((n) =>
      n === 1
        ? HttpResponse.json({ detail: 'product retired', successors: [21, 22] }, { status: 422 })
        : HttpResponse.json({ item_id: 100, nutrients: {} }),
    )
    server.use(
      http.get('*/products/21', () => HttpResponse.json({ detail: 'not found' }, { status: 404 })),
      http.get('*/products/22', () => HttpResponse.json({ detail: 'not found' }, { status: 404 })),
    )
    const { onClose } = renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('This product was replaced')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Use #22' }))
    await user.selectOptions(screen.getByLabelText('Unit'), 'g')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(calls[1]?.body.product_id).toBe(22)
    expect(calls[1]?.key).not.toBe(calls[0]?.key)
  })

  it('switch_to_successor_resets_unit_to_basis_unit', async () => {
    serveSearch()
    captureAdds((n) =>
      n === 1
        ? HttpResponse.json({ detail: 'product retired', successors: [22] }, { status: 422 })
        : HttpResponse.json({ item_id: 100, nutrients: {} }),
    )
    server.use(
      http.get('*/products/22', () =>
        HttpResponse.json({
          product_id: 22,
          name: 'Skyr',
          brand: null,
          basis_unit: 'ml',
          kind: 'product',
          values: {},
          portions: [],
          revisions: [],
          pending_reviews: [],
          overrides: [],
        }),
      ),
    )
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    const successorBtn = await screen.findByRole('button', { name: 'Use Skyr' })
    await user.click(successorBtn)
    await waitFor(() => expect(screen.getByLabelText('Unit')).toHaveValue('ml'))
  })

  it('switch_to_successor_submits_the_successor_unit_from_a_portion_chip', async () => {
    serveSearch()
    const calls = captureAdds((n) =>
      n === 1
        ? HttpResponse.json({ detail: 'product retired', successors: [22] }, { status: 422 })
        : HttpResponse.json({ item_id: 100, nutrients: {} }),
    )
    server.use(
      http.get('*/products/22', () =>
        HttpResponse.json({
          product_id: 22,
          name: 'Skyr',
          brand: null,
          basis_unit: 'ml',
          kind: 'product',
          values: {},
          portions: [{ label: '1 glass', amount: '200', kind: 'label' }],
          revisions: [],
          pending_reviews: [],
          overrides: [],
        }),
      ),
    )
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    const successorBtn = await screen.findByRole('button', { name: 'Use Skyr' })
    await user.click(successorBtn)
    await waitFor(() => expect(screen.getByLabelText('Unit')).toHaveValue('ml'))
    await user.click(await screen.findByRole('button', { name: '1 glass (200 ml)' }))
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1]?.body).toMatchObject({ product_id: 22, amount: '200', unit: 'ml' })
  })

  it('successor_button_shows_name_with_fallback_on_error', async () => {
    serveSearch()
    captureAdds(() => HttpResponse.json({ detail: 'product retired', successors: [21, 22] }, { status: 422 }))
    server.use(
      http.get('*/products/21', () =>
        HttpResponse.json({
          product_id: 21,
          name: 'Oat drink',
          brand: null,
          basis_unit: 'ml',
          kind: 'product',
          values: {},
          portions: [],
          revisions: [],
          pending_reviews: [],
          overrides: [],
        }),
      ),
      http.get('*/products/22', () => HttpResponse.json({ detail: 'not found' }, { status: 404 })),
    )
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('button', { name: 'Use Oat drink' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Use #22' })).toBeInTheDocument()
  })

  it('change_button_disabled_while_submit_busy', async () => {
    serveSearch()
    const deferred: { resolve: () => void } = { resolve: () => {} }
    server.use(
      http.post(`*/meals/${MEAL}/items`, async () => {
        await new Promise<void>((resolve) => {
          deferred.resolve = resolve
        })
        return HttpResponse.json({ item_id: 100, nutrients: {} })
      }),
    )
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '150')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Change' })).toBeDisabled())
    deferred.resolve()
  })

  it('converts_all_commas_in_amount', async () => {
    serveSearch()
    const calls = captureAdds()
    renderSheet()
    const user = userEvent.setup()
    await pickYogurt(user)
    await user.type(screen.getByLabelText('Amount'), '1,2,3')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.body.amount).toBe('1.2.3')
  })

  it('short_query_shows_recent_items', async () => {
    let searched = false
    server.use(
      http.get('*/recent-items', () =>
        HttpResponse.json([
          {
            product_id: 5,
            name: 'Oats',
            brand: null,
            basis_unit: 'g',
            last_amount: '40',
            last_unit: 'g',
            last_logged_at: '2026-10-12T08:00:00Z',
          },
        ]),
      ),
      http.get('*/search', () => {
        searched = true
        return HttpResponse.json([])
      }),
    )
    renderSheet()
    const user = userEvent.setup()
    expect(await screen.findByRole('button', { name: /Oats/ })).toBeInTheDocument()
    await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'o')
    await new Promise((r) => setTimeout(r, 300))
    expect(searched).toBe(false)
    expect(screen.getByRole('button', { name: /Oats/ })).toBeInTheDocument()
  })
})


it('search autofocuses and shows badges; tabs filter Dishes and Fast food', async () => {
  server.use(http.get('*/search', () => HttpResponse.json([
    yogurt,
    { ...yogurt, product_id: 12, name: 'Apple', brand: null },
    { type: 'meal', template_id: 9, name: 'Burger', name_en: null, chain: 'Grill', estimate: true },
    { type: 'meal', template_id: 10, name: 'Soup', name_en: null, chain: null, estimate: false },
  ])))
  renderSheet()
  const user = userEvent.setup()
  expect(screen.getByRole('searchbox')).toHaveFocus()
  await user.type(screen.getByRole('searchbox'), 'food')
  expect(await screen.findByText('Grill')).toBeInTheDocument()
  expect(screen.getByText('Estimate')).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Dishes' }))
  expect(screen.queryByText('Apple')).not.toBeInTheDocument()
  expect(screen.getByText('Soup')).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Fast food' }))
  expect(screen.getByText('Greek yogurt')).toBeInTheDocument()
  expect(screen.queryByText('Soup')).not.toBeInTheDocument()
})
it('portion chip then plus adds that amount; added tray totals and undo deletes the item', async () => {
  serveSearch()
  const calls = captureAdds(() => HttpResponse.json({ item_id: 100, nutrients: { energy_kcal: { known: true, lower: '165', upper: '165', qualifier: 'none' } } }))
  const deleted = vi.fn()
  server.use(http.delete('*/meal-items/100', () => { deleted(); return new HttpResponse(null, { status: 204 }) }))
  renderSheet()
  const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox'), 'yog')
  await user.click(await screen.findByRole('button', { name: '1 pot' }))
  await user.click(screen.getByRole('button', { name: 'Add Greek yogurt' }))
  expect(await screen.findByLabelText('Added total')).toHaveTextContent('165 kcal')
  expect(calls[0]?.body).toMatchObject({ amount: '170', quantity_source: 'label_portion' })
  await user.click(screen.getByRole('button', { name: 'Undo Greek yogurt' }))
  await waitFor(() => expect(deleted).toHaveBeenCalledOnce())
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Undo Greek yogurt' })).not.toBeInTheDocument())
})
it('done returns to the same day with items saved', async () => {
  serveSearch(); const calls = captureAdds()
  const close = vi.fn()
  renderWithProviders(<AddPage mealId={MEAL} day={DAY} onClose={close} />)
  const user = userEvent.setup()
  await pickYogurt(user)
  await user.type(screen.getByLabelText('Amount'), '150')
  await user.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByRole('button', { name: 'Undo Greek yogurt' })).toBeInTheDocument()
  expect(close).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Done' }))
  expect(close).toHaveBeenCalledOnce(); expect(calls).toHaveLength(1)
})

it('failed quick add keeps the same key and portion on retry', async () => {
  serveSearch()
  const calls = captureAdds((n) => n === 1 ? HttpResponse.json({ detail: 'try again' }, { status: 422 }) : HttpResponse.json({ item_id: 100, nutrients: {} }))
  renderSheet(); const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox'), 'yog')
  await user.click(await screen.findByRole('button', { name: '1 pot' }))
  await user.click(screen.getByRole('button', { name: 'Add Greek yogurt' }))
  expect(await screen.findByText('try again')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Add' }))
  await waitFor(() => expect(calls).toHaveLength(2))
  expect(calls[1]?.key).toBe(calls[0]?.key)
  expect(calls[1]?.body).toEqual(calls[0]?.body)
})
it('dish partial success appears in tray and retry adds only remaining ingredients', async () => {
  server.use(http.get('*/search', () => HttpResponse.json([{ type: 'meal', template_id: 9, name: 'Soup', name_en: null, chain: null, estimate: true }])),
    http.get('*/meal-templates/9', () => HttpResponse.json({ id: 9, name: 'Soup', name_en: null, chain: null, estimate: true, totals: {}, items: [{ product_id: 1, name: 'Carrot', name_en: null, grams: '80', nutrients: {} }, { product_id: 2, name: 'Beans', name_en: null, grams: '90', nutrients: {} }] })))
  const calls = captureAdds((n) => n === 2 ? HttpResponse.json({ detail: 'failed' }, { status: 422 }) : HttpResponse.json({ item_id: n, nutrients: {} }))
  renderSheet(); const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox'), 'soup')
  await user.click(await screen.findByRole('button', { name: /Soup/ }))
  await user.click(await screen.findByRole('button', { name: 'Add' }))
  expect(await screen.findByRole('button', { name: 'Undo Soup: Carrot' })).toBeInTheDocument()
  expect(await screen.findByRole('alert')).toHaveTextContent('Added 1 of 2')
  await user.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByRole('button', { name: 'Undo Soup: Beans' })).toBeInTheDocument()
  expect(calls).toHaveLength(3); expect(calls[2]?.key).toBe(calls[1]?.key)
})

it('Saved shows saved meals and Recent returns to recent items', async () => {
  server.use(http.get('*/saved-meals', () => HttpResponse.json([{ id: 8, name: 'Evening bowl', items: [] }])), http.get('*/recent-items', () => HttpResponse.json([{ product_id: 5, name: 'Oats', brand: null, basis_unit: 'g', last_amount: '40', last_unit: 'g' }])))
  renderSheet(); const user = userEvent.setup()
  expect(await screen.findByRole('button', { name: /Oats/ })).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Saved' }))
  expect(await screen.findByRole('button', { name: 'Evening bowl' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Oats/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Recent' }))
  expect(await screen.findByRole('button', { name: /Oats/ })).toBeInTheDocument()
})

it('saved additions remain undoable if refreshing their details fails', async () => {
  server.use(http.get('*/saved-meals', () => HttpResponse.json([{ id: 8, name: 'Evening bowl', items: [] }])),
    http.post('*/saved-meals/8/use', () => HttpResponse.json({ added: [81], blocked: [] })),
    http.get(`*/days/${DAY}`, () => HttpResponse.json({ detail: 'offline' }, { status: 503 })))
  renderSheet(); const user = userEvent.setup()
  await user.click(screen.getByRole('tab', { name: 'Saved' }))
  await user.click(await screen.findByRole('button', { name: 'Evening bowl' }))
  expect(await screen.findByRole('button', { name: 'Undo Saved meal ingredient' })).toBeInTheDocument()
  expect(screen.getByLabelText('Added total')).toHaveTextContent('?')
})

it('saved retry reuses its action key; intentional re-add starts a new action', async () => {
  const keys: (string | null)[] = []
  let fail = true
  server.use(http.get('*/saved-meals', () => HttpResponse.json([{ id: 8, name: 'Bowl', items: [] }])), http.post('*/saved-meals/8/use', async ({ request }) => {
    keys.push(request.headers.get('Idempotency-Key'))
    await new Promise((resolve) => setTimeout(resolve, 50))
    return fail ? HttpResponse.error() : HttpResponse.json({ added: [81], blocked: [] })
  }))
  renderSheet(); const user = userEvent.setup()
  await user.click(screen.getByRole('tab', { name: 'Saved' }))
  await user.dblClick(await screen.findByRole('button', { name: 'Bowl' }))
  await screen.findByRole('alert')
  expect(keys).toHaveLength(1)
  fail = false
  await user.click(screen.getByRole('button', { name: 'Bowl' }))
  await screen.findByRole('button', { name: 'Undo Saved meal ingredient' })
  expect(keys[1]).toBe(keys[0])
  await waitFor(() => expect(screen.getByRole('button', { name: 'Bowl' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Bowl' }))
  await waitFor(() => expect(keys).toHaveLength(3))
  expect(keys[2]).not.toBe(keys[1])
})
it('saved partial blocked retry does not repeat successful ingredients in tray', async () => {
  const keys: (string | null)[] = []
  server.use(http.get('*/saved-meals', () => HttpResponse.json([{ id: 8, name: 'Bowl', items: [] }])), http.post('*/saved-meals/8/use', ({ request }) => {
    keys.push(request.headers.get('Idempotency-Key'))
    return HttpResponse.json({ added: [81], blocked: [{ product_id: 2, reason: 'retired', successors: [3] }] })
  }))
  renderSheet(); const user = userEvent.setup()
  await user.click(screen.getByRole('tab', { name: 'Saved' }))
  await user.click(await screen.findByRole('button', { name: 'Bowl' }))
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: 'Bowl' }))
  await waitFor(() => expect(keys).toHaveLength(2))
  expect(keys[1]).toBe(keys[0])
  expect(screen.getAllByRole('button', { name: 'Undo Saved meal ingredient' })).toHaveLength(1)
})

it('named 100 g portion and default grams chip have exclusive selected states', async () => {
  serveSearch([{ ...yogurt, portions: [{ label: 'Small pot', amount: '100', kind: 'label' }] }])
  renderSheet(); const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox'), 'yog')
  const named = await screen.findByRole('button', { name: 'Small pot' })
  const grams = screen.getByRole('button', { name: '100 g' })
  await user.click(named)
  expect(named).toHaveAttribute('aria-pressed', 'true')
  expect(grams).toHaveAttribute('aria-pressed', 'false')
  await user.click(grams)
  expect(grams).toHaveAttribute('aria-pressed', 'true')
  expect(named).toHaveAttribute('aria-pressed', 'false')
})
