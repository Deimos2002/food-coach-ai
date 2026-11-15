import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { emptyDay } from '../../test/handlers'
import type { DayView } from '../../lib/types'
import { NutrientPanel } from './NutrientPanel'

function day(mod: (d: DayView) => void): DayView {
  const d = emptyDay('2026-10-13')
  mod(d)
  return d
}

describe('NutrientPanel', () => {
  it('nutrient_panel_shows_unknown_never_plain_total', () => {
    const d = day((d) => {
      d.nutrients.caffeine = {
        ...d.nutrients.caffeine,
        recorded: { lower: '80', upper: '80' },
        unresolved: ['Energy drink'],
      }
    })
    render(<NutrientPanel nutrients={d.nutrients} />)
    const row = screen.getByTestId('nutrient-caffeine')
    expect(row).toHaveTextContent('80 mg')
    expect(row).toHaveTextContent('unknown for: Energy drink')
  })

  it('unresolved_row_headline_is_lower_bound', () => {
    const d = day((d) => {
      d.nutrients.caffeine = {
        ...d.nutrients.caffeine,
        recorded: { lower: '80', upper: '80' },
        unresolved: ['Energy drink'],
      }
      d.nutrients.salt = { ...d.nutrients.salt, recorded: { lower: '7', upper: '7' } }
    })
    render(<NutrientPanel nutrients={d.nutrients} />)
    const row = screen.getByTestId('nutrient-caffeine')
    expect(row).toHaveTextContent('≥ 80 mg')
    expect(row).toHaveTextContent('unknown for: Energy drink')
    expect(screen.getByTestId('nutrient-salt')).toHaveTextContent(/^Salt 7 g$/)
  })

  it('nutrient_panel_trace_shows_open_range', () => {
    const d = day((d) => {
      d.nutrients.sugars = { ...d.nutrients.sugars, recorded: { lower: '0', upper: null }, unquantified: ['Cheese'] }
    })
    render(<NutrientPanel nutrients={d.nutrients} />)
    const row = screen.getByTestId('nutrient-sugars')
    expect(row).toHaveTextContent('≥ 0 g')
    expect(row).toHaveTextContent('trace: Cheese')
  })

  it('shows cooking additions not declared', () => {
    const d = day((d) => {
      d.nutrients.fat = { ...d.nutrients.fat, additions_undeclared: ['Fried egg'] }
    })
    render(<NutrientPanel nutrients={d.nutrients} />)
    expect(screen.getByTestId('nutrient-fat')).toHaveTextContent('cooking additions not declared: Fried egg')
  })

  it('completed_subtitle_only_when_complete', () => {
    const { rerender } = render(<NutrientPanel nutrients={emptyDay('2026-10-13').nutrients} />)
    expect(screen.getByRole('heading', { name: 'Recorded' })).toBeInTheDocument()
    expect(screen.queryByText('Based on your completed log')).not.toBeInTheDocument()
    const d = day((d) => {
      for (const n of Object.values(d.nutrients)) n.log_complete = true
    })
    rerender(<NutrientPanel nutrients={d.nutrients} />)
    expect(screen.getByText('Based on your completed log')).toBeInTheDocument()
  })

  it('hides caffeine and alcohol unless recorded or unresolved', () => {
    const d = day((d) => {
      d.nutrients.alcohol = { ...d.nutrients.alcohol, unresolved: ['Beer'] }
    })
    render(<NutrientPanel nutrients={d.nutrients} />)
    expect(screen.queryByTestId('nutrient-caffeine')).not.toBeInTheDocument()
    expect(screen.getByTestId('nutrient-alcohol')).toHaveTextContent('unknown for: Beer')
    expect(screen.getByTestId('nutrient-salt')).toHaveTextContent('0 g')
  })

  it('never_says_you_ate', () => {
    const { container } = render(<NutrientPanel nutrients={emptyDay('2026-10-13').nutrients} />)
    expect(container.textContent ?? '').not.toMatch(/you ate/i)
  })
})

it('exposes recorded nutrients as a named region', () => {
  render(<NutrientPanel nutrients={emptyDay('2026-10-13').nutrients} />)
  expect(screen.getByRole('region', { name: 'Recorded' })).toBeInTheDocument()
})
