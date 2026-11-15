import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { ProductSearch } from './ProductSearch'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const product = (over: Record<string, unknown>) => ({
  type: 'product', product_id: 1, name: 'Pomme', name_en: null, brand: null,
  basis_unit: 'g', portions: [], kcal_per_100: '52', ...over,
})

async function search(results: unknown[]) {
  server.use(http.get('*/search', () => HttpResponse.json(results)))
  const onPick = vi.fn()
  const onPickMeal = vi.fn()
  renderWithProviders(<ProductSearch onPick={onPick} onPickMeal={onPickMeal} />)
  await userEvent.setup().type(screen.getByRole('searchbox', { name: 'Search food' }), 'pom')
  return { onPick, onPickMeal }
}

describe('ProductSearch', () => {
  it('shows name_en with French fallback', async () => {
    const { onPick } = await search([
      product({ product_id: 1, name: 'Pomme', name_en: 'Apple' }),
      product({ product_id: 2, name: 'Fromage blanc' }),
    ])
    const apple = await screen.findByRole('button', { name: /Apple/ })
    expect(apple).toHaveTextContent('Pomme')
    expect(screen.getByRole('button', { name: /Fromage blanc/ })).toBeInTheDocument()
    await userEvent.setup().click(apple)
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ product_id: 1, name: 'Apple' }))
  })

  it('shows chain badge and estimate badge', async () => {
    const { onPickMeal } = await search([
      { type: 'meal', template_id: 9, name: 'Big Mac', name_en: null, cuisine: 'fast food', chain: 'McDonalds', estimate: true },
      { type: 'meal', template_id: 10, name: 'Tajine', name_en: 'Lamb tagine', cuisine: 'moroccan', chain: null, estimate: false },
    ])
    const bigMac = await screen.findByRole('button', { name: /Big Mac/ })
    expect(bigMac).toHaveTextContent('McDonalds')
    expect(bigMac).toHaveTextContent('Estimate')
    const tagine = screen.getByRole('button', { name: /Lamb tagine/ })
    expect(tagine).not.toHaveTextContent('Estimate')
    await userEvent.setup().click(bigMac)
    expect(onPickMeal).toHaveBeenCalledWith(9)
  })
})
