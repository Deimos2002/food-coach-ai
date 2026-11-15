import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'
import type { DayView, Targets } from '../../lib/types'
import { nutrientDisplayValue } from '../../lib/format'
import { Card } from '../../ui/Card'
import { Ring } from '../../ui/Ring'
import { MacroBar } from '../../ui/MacroBar'

export function DaySummary({ nutrients, targets, children }: { nutrients: DayView['nutrients']; targets?: Targets; children?: ReactNode }) {
  return <Card as="section" className="day-summary">
    <div className="day-summary-heading">
      <span className="day-summary-kicker">Your day, in balance</span>
      <h2>Daily summary</h2>
    </div>
    <div className="day-summary-content">
      <div className="day-summary-values">
        <Ring value={nutrientDisplayValue(nutrients.energy_kcal, 0)} target={targets?.kcal ?? null} label="Energy" unit="kcal" />
        <div className="day-summary-macros">
          <MacroBar label="Protein" value={nutrientDisplayValue(nutrients.protein, 1)} target={targets?.protein_g ?? null} unit="g" tone="protein" />
          <MacroBar label="Carbs" value={nutrientDisplayValue(nutrients.carbohydrate, 1)} target={null} unit="g" tone="carbs" />
          <MacroBar label="Fat" value={nutrientDisplayValue(nutrients.fat, 1)} target={null} unit="g" tone="fat" />
        </div>
      </div>
      {!targets && <Link className="day-target-link" to="/me">Set your targets</Link>}
      {children}
    </div>
  </Card>
}

