import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { MealTemplateSheet } from './MealTemplateSheet'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const nut = (v: string) => ({ known: true, lower: v, upper: v })
const template = {
  id: 9, name: 'Poulet frites', name_en: 'Chicken and chips', cuisine: 'bistro', chain: 'Quick', estimate: true,
  source_url: null,
  items: [
    { product_id: 1, name: 'Poulet', name_en: 'Chicken', grams: '100', nutrients: { energy_kcal: nut('200') } },
    { product_id: 2, name: 'Frites', name_en: null, grams: '150', nutrients: { energy_kcal: nut('300') } },
  ],
  totals: { energy_kcal: nut('500') },
}

function setup() {
  server.use(http.get('*/meal-templates/9', () => HttpResponse.json(template)))
  const posts: Record<string, unknown>[] = []
  server.use(
    http.post('*/meals/7/items', async ({ request }) => {
      posts.push((await request.json()) as Record<string, unknown>)
      return HttpResponse.json({ item_id: 100 + posts.length, nutrients: {} })
    }),
  )
  const onClose = vi.fn()
  renderWithProviders(<MealTemplateSheet templateId={9} mealId={7} day="2026-10-13" onClose={onClose} onBack={vi.fn()} />)
  return { posts, onClose }
}

describe('MealTemplateSheet', () => {
  it('meal sheet lets grams change and an ingredient be removed before add', async () => {
    setup()
    const user = userEvent.setup()
    await screen.findByText('Chicken')
    expect(screen.getByText('Frites')).toBeInTheDocument()
    expect(screen.getByTestId('totals')).toHaveTextContent('500')
    const grams = screen.getByRole('textbox', { name: 'Grams of Chicken' })
    await user.clear(grams)
    await user.type(grams, '50')
    expect(screen.getByTestId('totals')).toHaveTextContent('400')
    await user.click(screen.getByRole('button', { name: 'Remove Frites' }))
    expect(screen.queryByText('Frites')).not.toBeInTheDocument()
    expect(screen.getByTestId('totals')).toHaveTextContent('100')
  })

  it('add posts one item per remaining ingredient', async () => {
    const { posts, onClose } = setup()
    const user = userEvent.setup()
    await screen.findByText('Chicken')
    await user.click(screen.getByRole('button', { name: 'Remove Frites' }))
    const grams = screen.getByRole('textbox', { name: 'Grams of Chicken' })
    await user.clear(grams)
    await user.type(grams, '120')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(posts).toEqual([
      { product_id: 1, amount: '120', unit: 'g', quantity_source: 'user_estimate', dish: 'Chicken and chips' },
    ])
  })

  it('shows the estimate badge and chain', async () => {
    setup()
    const head = await screen.findByRole('heading', { name: /Chicken and chips/ })
    expect(within(head.parentElement as HTMLElement).getByText('Estimate')).toBeInTheDocument()
    expect(screen.getByText('Quick')).toBeInTheDocument()
  })

  it('two ingredients post two items with dish and grams', async () => {
    const { posts, onClose } = setup()
    const user = userEvent.setup()
    await screen.findByText('Chicken')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(posts).toEqual([
      { product_id: 1, amount: '100', unit: 'g', quantity_source: 'user_estimate', dish: 'Chicken and chips' },
      { product_id: 2, amount: '150', unit: 'g', quantity_source: 'user_estimate', dish: 'Chicken and chips' },
    ])
  })

  it('a failing second post invalidates the day, locks the first row and retries only the rest', async () => {
    server.use(http.get('*/meal-templates/9', () => HttpResponse.json(template)))
    const posts: number[] = []
    let fail = true
    server.use(
      http.post('*/meals/7/items', async ({ request }) => {
        const b = (await request.json()) as { product_id: number }
        if (b.product_id === 2 && fail) return HttpResponse.json({ detail: 'boom' }, { status: 400 })
        posts.push(b.product_id)
        return HttpResponse.json({ item_id: 1, nutrients: {} })
      }),
    )
    const onClose = vi.fn()
    const { queryClient } = renderWithProviders(
      <MealTemplateSheet templateId={9} mealId={7} day="2026-10-13" onClose={onClose} onBack={vi.fn()} />,
    )
    const inv = vi.spyOn(queryClient, 'invalidateQueries')
    const user = userEvent.setup()
    await screen.findByText('Chicken')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('1 of 2')
    expect(inv).toHaveBeenCalledWith({ queryKey: ['day', '2026-10-13'] })
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'Grams of Chicken' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Remove Chicken' })).not.toBeInTheDocument()
    fail = false
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(posts).toEqual([1, 2])
  })

  it('empty grams make totals unknown and disable Add', async () => {
    setup()
    const user = userEvent.setup()
    await screen.findByText('Chicken')
    await user.clear(screen.getByRole('textbox', { name: 'Grams of Chicken' }))
    expect(screen.getByTestId('totals')).toHaveTextContent('Energy ?')
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
  })
})

it('legacy template opens a fixed modal while full-page context uses a panel', async () => {
  const view = renderWithProviders(<MealTemplateSheet templateId={9} mealId={7} day="2026-10-13" onClose={vi.fn()} onBack={vi.fn()} />)
  expect(screen.getByRole('dialog', { name: 'Add meal' })).toHaveAttribute('aria-modal', 'true')
  expect(screen.getByRole('dialog', { name: 'Add meal' })).toHaveClass('add-sheet')
  view.unmount()
  renderWithProviders(<MealTemplateSheet panel templateId={9} mealId={7} day="2026-10-13" onClose={vi.fn()} onBack={vi.fn()} />)
  expect(screen.getByRole('dialog', { name: 'Add meal' })).toHaveAttribute('aria-modal', 'false')
  expect(screen.getByRole('dialog', { name: 'Add meal' })).not.toHaveClass('add-sheet')
})
