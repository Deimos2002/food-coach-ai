import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { ProductSearch } from './ProductSearch'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('shows supermarket products in general search and keeps them out of the restaurant tab', async () => {
  server.use(http.get('*/search', () => HttpResponse.json([
    { type: 'product', product_id: 1, name: 'Monster Mango Loco', brand: 'Monster Energy', basis_unit: 'ml', portions: [], kcal_per_100: '46', is_fast_food: false },
    { type: 'product', product_id: 2, name: 'Big Mac', brand: "McDonald's", basis_unit: 'g', portions: [], kcal_per_100: '250', is_fast_food: true },
  ])))
  renderWithProviders(<ProductSearch onPick={vi.fn()} />)
  const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox', { name: 'Search food' }), 'food')
  expect(await screen.findByText('Monster Mango Loco')).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Fast food' }))
  expect(screen.queryByText('Monster Mango Loco')).not.toBeInTheDocument()
  expect(screen.getByText('Big Mac')).toBeInTheDocument()
})
