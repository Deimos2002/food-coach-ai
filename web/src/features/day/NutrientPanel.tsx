import { Card } from '../../ui/Card'
import { formatRange } from '../../lib/format'
import type { Dec, DayView, Nutrient, NutrientState } from '../../lib/types'

interface Row {
  key: Nutrient
  label: string
  unit: string
  optional?: boolean
}

const ROWS: Row[] = [
  { key: 'energy_kcal', label: 'Energy', unit: 'kcal' },
  { key: 'protein', label: 'Protein', unit: 'g' },
  { key: 'carbohydrate', label: 'Carbohydrate', unit: 'g' },
  { key: 'sugars', label: 'Sugars', unit: 'g' },
  { key: 'fat', label: 'Fat', unit: 'g' },
  { key: 'saturated_fat', label: 'Saturated fat', unit: 'g' },
  { key: 'fibre', label: 'Fibre', unit: 'g' },
  { key: 'salt', label: 'Salt', unit: 'g' },
  { key: 'caffeine', label: 'Caffeine', unit: 'mg', optional: true },
  { key: 'alcohol', label: 'Alcohol', unit: 'g', optional: true },
]

/** A decimal string that is not a literal zero (no arithmetic: a string check). Null counts as "something". */
const notZero = (d: Dec | null) => d === null || !/^-?0*(\.0*)?$/.test(d)

/** Optional rows appear only when something was recorded (nonzero or open bound) or is unresolved. */
function hasSomething(s: NutrientState): boolean {
  return s.unresolved.length > 0 || s.unquantified.length > 0 || notZero(s.recorded.lower) || notZero(s.recorded.upper)
}

export function NutrientPanel({ nutrients }: { nutrients: DayView['nutrients'] }) {
  const logComplete = Object.values(nutrients).some((s) => s.log_complete)
  return (
    <Card className="nutrient-panel"><section aria-labelledby="recorded-heading">
      <h2 id="recorded-heading">Recorded</h2>
      {logComplete && <p>Based on your completed log</p>}
      <details>
        <summary>View nutrient breakdown</summary>
        <ul>
        {ROWS.map(({ key, label, unit, optional }) => {
          const s = nutrients[key]
          if (!s || (optional && !hasSomething(s))) return null
          const notes: string[] = []
          if (s.unresolved.length > 0) notes.push(`unknown for: ${s.unresolved.join(', ')}`)
          if (s.unquantified.length > 0) notes.push(`trace: ${s.unquantified.join(', ')}`)
          if (s.additions_undeclared.length > 0)
            notes.push(`cooking additions not declared: ${s.additions_undeclared.join(', ')}`)
          // With unknown or trace contributions the true total is only bounded below.
          const open = s.unresolved.length > 0 || s.unquantified.length > 0
          const headline = formatRange(s.recorded.lower, open ? null : s.recorded.upper, unit)
          return (
            <li key={key} data-testid={`nutrient-${key}`}>
              <span>{label}</span> <strong>{headline}</strong>
              {notes.map((n) => (
                <span key={n}> · {n}</span>
              ))}
            </li>
          )
        })}
        </ul>
      </details>
    </section></Card>
  )
}
