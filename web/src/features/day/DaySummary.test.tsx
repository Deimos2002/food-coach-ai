import { screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { emptyDay } from '../../test/handlers'
import { DaySummary } from './DaySummary'

it('summary shows ring and three macro bars against targets', () => {
  const nutrients = emptyDay('2026-10-13').nutrients
  nutrients.energy_kcal.recorded = { lower: '650', upper: '650' }
  nutrients.protein.recorded = { lower: '42', upper: '42' }
  renderWithProviders(<DaySummary nutrients={nutrients} targets={{ kcal: 2100, maintenance_kcal: 2400, protein_g: 120 }} />)
  expect(screen.getByRole('img', { name: 'Energy 650 of 2100 kcal' })).toBeInTheDocument()
  expect(screen.getByText('42 / 120 g')).toBeInTheDocument()
  expect(screen.getByText('Carbs')).toBeInTheDocument()
  expect(screen.getByText('Fat')).toBeInTheDocument()
  expect(screen.getAllByText('0 / ? g')).toHaveLength(2)
})

it('summary without targets prompts to set them', () => {
  renderWithProviders(<DaySummary nutrients={emptyDay('2026-10-13').nutrients} />)
  expect(screen.getByRole('link', { name: 'Set your targets' })).toHaveAttribute('href', '/me')
})

it('unknown day total shows ?', () => {
  const nutrients = emptyDay('2026-10-13').nutrients
  nutrients.energy_kcal.recorded = { lower: '650', upper: '650' }
  nutrients.energy_kcal.unresolved = ['Sauce']
  renderWithProviders(<DaySummary nutrients={nutrients} targets={{ kcal: 2100, maintenance_kcal: 2400, protein_g: 120 }} />)
  expect(screen.getByRole('img', { name: 'Energy ? of 2100 kcal' })).toBeInTheDocument()
})

it('summary rounds fractional energy and macros for readable display', () => {
  const nutrients = emptyDay('2026-10-13').nutrients
  nutrients.energy_kcal.recorded = { lower: '694.99936', upper: '694.99936' }
  nutrients.protein.recorded = { lower: '62.10016', upper: '62.10016' }
  renderWithProviders(<DaySummary nutrients={nutrients} />)
  expect(screen.getByRole('img', { name: 'Energy 695 of ? kcal' })).toBeInTheDocument()
  expect(screen.getByText('695')).toBeInTheDocument()
  expect(screen.getByText('62.1 / ? g')).toBeInTheDocument()
})
