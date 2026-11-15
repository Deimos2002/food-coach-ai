import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { emptyDay } from '../../test/handlers'
import { NutrientPanel } from './NutrientPanel'

it('keeps the detailed nutrient breakdown tucked away until requested', () => {
  render(<NutrientPanel nutrients={emptyDay('2026-10-15').nutrients} />)
  const toggle = screen.getByText('View nutrient breakdown')
  const details = toggle.closest('details')!
  expect(details).not.toHaveAttribute('open')
  fireEvent.click(toggle)
  expect(details).toHaveAttribute('open')
  expect(screen.getByTestId('nutrient-salt')).toBeVisible()
})
