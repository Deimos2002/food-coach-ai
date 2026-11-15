import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { Route, Routes, useLocation } from 'react-router-dom'
import { renderWithProviders } from '../../test/render'
import { server } from '../../test/handlers'
import type { Product } from '../../lib/types'
import { NewProductForm } from './NewProductForm'
import { OffImport } from './OffImport'
import { ProductPage } from './ProductPage'

/** Reports where it was routed to and with what state, for asserting the return-to-day fix. */
function LocationProbe() {
  const location = useLocation()
  return <p data-testid="probe">{JSON.stringify({ pathname: location.pathname, state: location.state })}</p>
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => { cleanup(); server.resetHandlers() })
afterAll(() => server.close())

function product(owner: 'user' | 'catalogue' = 'user'): Product {
  return {
    product_id: 7, owner, name: 'Soup', brand: null, kind: 'dish', basis_unit: 'g',
    includes_cooking_additions: 'unanswered', portions: [],
    values: { salt: { known: true, lower: '1', upper: '2', qualifier: 'none', source: 'manual', verification: 'user_verified' } } as Product['values'],
    revisions: [{ id: 9, status: 'pending_review', created_at: '2026-10-13T12:00:00Z' }],
    pending_reviews: [9],
    overrides: [{ id: 11, version: 1, nutrient: 'salt', value: { known: false, lower: null, upper: null, qualifier: 'none' }, pinned_revision_id: 8, retain_across_revisions: false, needs_reconfirm: true }],
  }
}

function detail(data = product()) {
  server.use(http.get('*/products/7', () => HttpResponse.json(data)))
  return renderWithProviders(<Routes><Route path="/products/:id" element={<ProductPage />} /></Routes>, { route: '/products/7' })
}

it('empty_nutrient_is_sent_unknown_not_zero', async () => {
  let body: unknown
  server.use(http.post('*/products', async ({ request }) => {
    body = await request.json()
    return HttpResponse.json({ product_id: 7 }, { status: 201 })
  }))
  renderWithProviders(<NewProductForm />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Soup')
  await user.click(screen.getByLabelText('Salt known'))
  await user.click(screen.getByRole('button', { name: 'Create product' }))
  await waitFor(() => expect(body).toBeDefined())
  expect(body).toMatchObject({ name: 'Soup', kind: 'product', basis_unit: 'g', values: { salt: null, caffeine: null } })
})

it('dish_asks_cooking_additions', async () => {
  let body: unknown
  server.use(http.post('*/products', async ({ request }) => {
    body = await request.json()
    return HttpResponse.json({ product_id: 7 }, { status: 201 })
  }))
  renderWithProviders(<NewProductForm />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Soup')
  await user.selectOptions(screen.getByLabelText('Kind'), 'dish')
  await user.selectOptions(screen.getByLabelText('Does this include cooking oil/salt?'), 'yes')
  await user.click(screen.getByRole('button', { name: 'Create product' }))
  await waitFor(() => expect(body).toMatchObject({ kind: 'dish', includes_cooking_additions: 'yes' }))
})

it('off_import_needs_basis_prompts_then_retries', async () => {
  const bases: (string | null)[] = []
  server.use(http.post('*/products/import-off/123', ({ request }) => {
    const basis = new URL(request.url).searchParams.get('basis')
    bases.push(basis)
    return basis ? HttpResponse.json({ product_id: 7 }) : HttpResponse.json({ detail: 'needs basis', needs_basis: true }, { status: 409 })
  }))
  renderWithProviders(<OffImport />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Barcode'), '123')
  await user.click(screen.getByRole('button', { name: 'Import barcode' }))
  expect(await screen.findByText('Per 100 g or per 100 ml?')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Per 100 ml' }))
  await waitFor(() => expect(bases).toEqual([null, 'ml']))
})

it('accept_hidden_for_catalogue_product', async () => {
  detail(product('catalogue'))
  await screen.findByRole('heading', { name: 'Soup' })
  expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
})

it('reconfirm_button_calls_api', async () => {
  let called = false
  server.use(http.post('*/overrides/11/reconfirm', () => {
    called = true
    return HttpResponse.json({ override_id: 11, pinned_revision_id: 9 })
  }))
  detail()
  await userEvent.click(await screen.findByRole('button', { name: 'Reconfirm' }))
  await waitFor(() => expect(called).toBe(true))
})

it('known_zero_and_comma_values_are_sent_as_strings', async () => {
  let body: unknown
  server.use(http.post('*/products', async ({ request }) => {
    body = await request.json()
    return HttpResponse.json({ product_id: 7 }, { status: 201 })
  }))
  renderWithProviders(<NewProductForm />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Drink')
  await user.click(screen.getByLabelText('Salt known'))
  await user.type(screen.getByLabelText('Salt per 100 g'), '0')
  await user.click(screen.getByLabelText('Sugars known'))
  await user.type(screen.getByLabelText('Sugars per 100 g'), '12,5')
  await user.click(screen.getByRole('button', { name: 'Create product' }))
  await waitFor(() => expect(body).toMatchObject({ values: { salt: { value: '0' }, sugars: { value: '12.5' } } }))
})

it('off_name_search_imports_selected_barcode', async () => {
  let imported = false
  server.use(
    http.get('*/off/search', ({ request }) => {
      expect(new URL(request.url).searchParams.get('q')).toBe('peach')
      return HttpResponse.json([{ code: '123', product_name: 'Peach drink' }])
    }),
    http.post('*/products/import-off/123', () => {
      imported = true
      return HttpResponse.json({ product_id: 7 })
    }),
  )
  renderWithProviders(<OffImport />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('OFF name search'), 'peach')
  await user.click(screen.getByRole('button', { name: 'Search Open Food Facts' }))
  await user.click(await screen.findByRole('button', { name: 'Import Peach drink' }))
  await waitFor(() => expect(imported).toBe(true))
})

it('user_can_accept_pending_revision_and_create_override', async () => {
  let accepted = false
  let correction: unknown
  server.use(
    http.post('*/products/7/revisions/9/accept', () => {
      accepted = true
      return HttpResponse.json({ product_id: 7, revision_id: 9, status: 'adopted' })
    }),
    http.post('*/products/7/overrides', async ({ request }) => {
      correction = await request.json()
      return HttpResponse.json({ override_id: 12 }, { status: 201 })
    }),
  )
  detail()
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Accept' }))
  await waitFor(() => expect(accepted).toBe(true))
  await user.type(screen.getByLabelText('Corrected value per 100 g'), '0,5')
  await user.type(screen.getByLabelText('Reason'), 'Label correction')
  await user.click(screen.getByRole('button', { name: 'Save correction' }))
  await waitFor(() => expect(correction).toEqual({ nutrient: 'salt', value: { value: '0.5' }, reason: 'Label correction', retain_across_revisions: false }))
})

it('dish_can_update_cooking_additions_and_refresh_day_queries', async () => {
  let body: unknown
  server.use(http.put('*/products/7/cooking-additions', async ({ request }) => {
    body = await request.json()
    return HttpResponse.json({ product_id: 7, includes_cooking_additions: 'no' })
  }))
  const { queryClient } = detail()
  queryClient.setQueryData(['day', '2026-10-13'], {})
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByLabelText('Does this include cooking oil/salt?'), 'no')
  await user.click(screen.getByRole('button', { name: 'Save cooking additions' }))
  await waitFor(() => expect(body).toEqual({ value: 'no' }))
  await waitFor(() => expect(queryClient.getQueryState(['day', '2026-10-13'])?.isInvalidated).toBe(true))
})

it('create_product_with_meal_and_day_params_returns_to_day_with_sheet_reopen_state', async () => {
  server.use(http.post('*/products', () => HttpResponse.json({ product_id: 7 }, { status: 201 })))
  renderWithProviders(
    <Routes>
      <Route path="/products/new" element={<NewProductForm />} />
      <Route path="/day/:day" element={<LocationProbe />} />
    </Routes>,
    { route: '/products/new?meal=7&day=2099-12-25' },
  )
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Soup')
  await user.click(screen.getByRole('button', { name: 'Create product' }))
  const probe = await screen.findByTestId('probe')
  expect(JSON.parse(probe.textContent ?? '{}')).toEqual({
    pathname: '/day/2099-12-25',
    state: { reopenMealId: 7, productId: 7 },
  })
})

it('create_product_without_params_keeps_the_old_behaviour', async () => {
  server.use(http.post('*/products', () => HttpResponse.json({ product_id: 7 }, { status: 201 })))
  renderWithProviders(
    <Routes>
      <Route path="/products/new" element={<NewProductForm />} />
      <Route path="/products/:id" element={<LocationProbe />} />
    </Routes>,
    { route: '/products/new' },
  )
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Soup')
  await user.click(screen.getByRole('button', { name: 'Create product' }))
  const probe = await screen.findByTestId('probe')
  expect(JSON.parse(probe.textContent ?? '{}')).toEqual({ pathname: '/products/7', state: null })
})

it('off_import_with_meal_and_day_params_returns_to_day_with_sheet_reopen_state', async () => {
  server.use(http.post('*/products/import-off/123', () => HttpResponse.json({ product_id: 9 })))
  renderWithProviders(
    <Routes>
      <Route path="/products/new" element={<OffImport />} />
      <Route path="/day/:day" element={<LocationProbe />} />
    </Routes>,
    { route: '/products/new?meal=3&day=2099-12-25' },
  )
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Barcode'), '123')
  await user.click(screen.getByRole('button', { name: 'Import barcode' }))
  const probe = await screen.findByTestId('probe')
  expect(JSON.parse(probe.textContent ?? '{}')).toEqual({
    pathname: '/day/2099-12-25',
    state: { reopenMealId: 3, productId: 9 },
  })
})

it('detail_displays_unknowns_ranges_and_provenance', async () => {
  const data = product()
  data.values.sugars = { known: true, lower: '0', upper: null, qualifier: 'trace', source: 'ciqual', verification: 'unverified' }
  detail(data)
  expect(await screen.findByText(/1–2 g · manual · user_verified/)).toBeInTheDocument()
  expect(screen.getByText(/≥ 0 g · ciqual · unverified · trace/)).toBeInTheDocument()
  expect(screen.getByText(/\? · version 1/)).toBeInTheDocument()
  expect(screen.getByText('caffeine', { selector: 'dt' })).toBeInTheDocument()
})
