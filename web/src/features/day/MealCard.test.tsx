import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../test/render'
import type { ItemView } from '../../lib/types'
import { MealCard } from './MealCard'

const item = (id: number, name: string, dish?: string): ItemView => ({
  item_id: id, name, requested_product_id: id, resolved_product_id: id, profile_revision_id: 1,
  quantity: { kind: 'recorded', value: '100', lower: null, upper: null, unit: 'g', source: 'weighed' },
  nutrients: {}, logged_at: '2026-10-13T08:00:00Z', consumed_at: null, dish,
})

describe('MealCard', () => {
  it('day view lists ingredients under the dish name', () => {
    renderWithProviders(
      <MealCard
        mealType="lunch"
        meal={{ meal_id: 7, meal_type: 'lunch', skipped: false, items: [
          item(1, 'Poulet', 'Chicken and chips'), item(2, 'Apple'), item(3, 'Frites', 'Chicken and chips'),
        ] }}
        onAdd={vi.fn()}
        onSetSkipped={vi.fn()}
      />,
    )
    const dish = screen.getByRole('heading', { name: 'Chicken and chips' })
    const group = within(dish.closest('li') as HTMLElement)
    expect(group.getByText('Poulet')).toBeInTheDocument()
    expect(group.getByText('Frites')).toBeInTheDocument()
    expect(group.queryByText('Apple')).not.toBeInTheDocument()
    expect(screen.getByText('Apple')).toBeInTheDocument()
  })
})

it('meal card plus links to add route', () => {
  renderWithProviders(<MealCard mealType="lunch" day="2026-10-13" meal={{ meal_id: 7, meal_type: 'lunch', skipped: false, items: [] }} onAdd={() => {}} onSetSkipped={() => {}} />)
  expect(screen.getByRole('link', { name: 'Add to Lunch' })).toHaveAttribute('href', '/add/2026-10-13/7')
})

it('meal card shows its recorded energy total and unknown contributions', () => {
  const oats = item(1, 'Oats')
  oats.nutrients.energy_kcal = { known: true, lower: '150', upper: '150', qualifier: 'none' }
  const { rerender } = renderWithProviders(<MealCard mealType="breakfast" day="2026-10-13" meal={{ meal_id: 7, meal_type: 'breakfast', skipped: false, items: [oats] }} onAdd={() => {}} onSetSkipped={() => {}} />)
  expect(screen.getByLabelText('Meal energy')).toHaveTextContent('150 kcal')
  rerender(<MealCard mealType="breakfast" day="2026-10-13" meal={{ meal_id: 7, meal_type: 'breakfast', skipped: false, items: [oats, item(2, 'Sauce')] }} onAdd={() => {}} onSetSkipped={() => {}} />)
  expect(screen.getByLabelText('Meal energy')).toHaveTextContent('?')
})
