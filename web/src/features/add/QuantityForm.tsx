import './AddPage.css'
import { Button } from '../../ui/Button'
import { formatDec } from '../../lib/format'
import type { Portion, QuantitySource, Unit } from '../../lib/types'

export interface QuantityValue {
  amount: string
  /** Empty when a successor's basis unit is unknown: the user must choose. */
  unit: Unit | ''
  source: QuantitySource
  range: boolean
  lower: string
  upper: string
}

const SOURCES: { value: QuantitySource; label: string }[] = [
  { value: 'user_estimate', label: 'Estimate' },
  { value: 'weighed', label: 'Weighed' },
  { value: 'package', label: 'Whole package' },
  { value: 'label_portion', label: 'Label portion' },
]

export interface QuantityFormProps {
  value: QuantityValue
  portions: Portion[]
  basisUnit: Unit
  onChange: (v: QuantityValue) => void
}


export function QuantityForm({ value, portions, basisUnit, onChange }: QuantityFormProps) {
  const set = (patch: Partial<QuantityValue>) => onChange({ ...value, ...patch })
  return (
    <fieldset className="quantity-form">
      {portions.length > 0 && (
        <div role="group" aria-label="Portions" className="portion-chips">
          {portions.map((p) => (
            <Button variant="secondary"
              key={p.label}
              type="button"

              onClick={() => set({ amount: String(p.amount), unit: basisUnit, source: 'label_portion' })}
            >
              {`${p.label} (${formatDec(String(p.amount))} ${basisUnit})`}
            </Button>
          ))}
        </div>
      )}
      <label>
        Amount
        <input
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={value.amount}
          onChange={(e) => set({ amount: e.target.value })}

        />
      </label>
      <label>
        Unit
        <select
          value={value.unit}
          required
          onChange={(e) => set({ unit: e.target.value as Unit })}

        >
          {value.unit === '' && (
            <option value="" disabled>
              Choose unit
            </option>
          )}
          <option value="g">g</option>
          <option value="ml">ml</option>
        </select>
      </label>
      <label>
        Source
        <select
          value={value.source}
          onChange={(e) => set({ source: e.target.value as QuantitySource })}

        >
          {SOURCES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <label >
        <input type="checkbox" checked={value.range} onChange={(e) => set({ range: e.target.checked })} />
        Estimate range
      </label>
      {value.range && (
        <>
          <label>
            Lower
            <input
              type="text"
              inputMode="decimal"
              value={value.lower}
              onChange={(e) => set({ lower: e.target.value })}

            />
          </label>
          <label>
            Upper
            <input
              type="text"
              inputMode="decimal"
              value={value.upper}
              onChange={(e) => set({ upper: e.target.value })}

            />
          </label>
        </>
      )}
    </fieldset>
  )
}
