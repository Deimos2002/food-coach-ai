import './AddPage.css'
import { Button } from '../../ui/Button'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { addItem, getMealTemplate } from '../../lib/api'
import { formatRange, scaledTotal } from '../../lib/format'
import { newKey } from '../../lib/idempotency'
import type { AddedItem } from './AddedTray'
import type { IsoDay, MealTemplate, Nutrient } from '../../lib/types'

const SHOWN: [Nutrient, string, string][] = [
  ['energy_kcal', 'Energy', 'kcal'],
  ['protein', 'Protein', 'g'],
  ['carbohydrate', 'Carbs', 'g'],
  ['fat', 'Fat', 'g'],
]

const validGrams = (s: string) => /^\d+([.,]\d+)?$/.test(s.trim()) && /[1-9]/.test(s)
const toDec = (s: string) => s.trim().replaceAll(',', '.')

/** Ingredient list for a picked meal template: edit grams, drop ingredients, then add them all. */
export function MealTemplateSheet({
  templateId,
  mealId,
  day,
  onClose,
  onBack,
  onAdded,
  panel = false,
}: {
  panel?: boolean
  templateId: number
  mealId: number
  day: IsoDay
  onClose: () => void
  onBack: () => void
  onAdded?: (item: AddedItem) => void
}) {
  const q = useQuery({ queryKey: ['meal-template', templateId], queryFn: () => getMealTemplate(templateId) })
  return (
    <div className={panel ? 'meal-template-panel' : 'add-sheet meal-template-modal'} role="dialog" aria-modal={!panel} aria-label="Add meal">
      <div>
        <Button variant="secondary" type="button" onClick={onBack}>
          Back
        </Button>
        <Button variant="secondary" type="button" onClick={onClose}>
          Close
        </Button>
      </div>
      {q.error && <p role="alert">{q.error.message}</p>}
      {q.data && <Body t={q.data} mealId={mealId} day={day} onClose={onClose} onAdded={onAdded} />}
    </div>
  )
}

function Body({ t, mealId, day, onClose, onAdded }: { t: MealTemplate; mealId: number; day: IsoDay; onClose: () => void; onAdded?: (item: AddedItem) => void }) {
  const qc = useQueryClient()
  const [grams, setGrams] = useState<string[]>(() => t.items.map((i) => i.grams))
  const [removed, setRemoved] = useState<Set<number>>(new Set())
  const [added, setAdded] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // One key per ingredient and grams value: a retry after a partial failure re-sends the same
  // keys, so ingredients already added are not added twice.
  const keys = useRef(new Map<string, string>())
  const dish = t.name_en ?? t.name

  const rows = t.items.map((item, i) => ({ item, i })).filter(({ i }) => !removed.has(i))
  const ok = rows.length > 0 && rows.every(({ i }) => validGrams(grams[i]!))

  const add = async () => {
    setBusy(true)
    setError(null)
    let done = added.size
    try {
      for (const { item, i } of rows) {
        if (added.has(i)) continue
        const k = `${i}:${toDec(grams[i]!)}`
        if (!keys.current.has(k)) keys.current.set(k, newKey())
        const result = await addItem(
          mealId,
          { product_id: item.product_id, amount: toDec(grams[i]!), unit: 'g', quantity_source: 'user_estimate', dish },
          keys.current.get(k)!,
        )
        onAdded?.({ ...result, name: `${dish}: ${item.name_en ?? item.name}` })
        done++
        setAdded((a) => new Set(a).add(i))
      }
      onClose()
    } catch (e) {
      setError(`Added ${done} of ${rows.length} ingredients: ${e instanceof Error ? e.message : 'could not add'}`)
      setBusy(false)
    } finally {
      // Show whatever was added, even when a later POST failed.
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['day', day] }),
        qc.invalidateQueries({ queryKey: ['recent'] }),
      ])
    }
  }

  const untouched = removed.size === 0 && t.items.every((item, i) => grams[i] === item.grams)

  return (
    <div>
      <div>
        <h2>{dish}</h2>
        {t.chain && <span className="chip">{t.chain}</span>}
        {t.estimate && <span className="chip">Estimate</span>}
      </div>
      <ul aria-label="Ingredients">
        {rows.map(({ item, i }) => (
          <li key={i}>
            <span>{item.name_en ?? item.name}</span>{' '}
            <input
              type="text"
              inputMode="decimal"
              aria-label={`Grams of ${item.name_en ?? item.name}`}
              value={grams[i]}
              disabled={added.has(i)}
              onChange={(e) => setGrams((g) => g.map((v, j) => (j === i ? e.target.value : v)))}

            />{' '}
            g{' '}
            {added.has(i) ? (
              <span>Added</span>
            ) : (
            <Button variant="secondary"
              type="button"
              aria-label={`Remove ${item.name_en ?? item.name}`}
              onClick={() => setRemoved((r) => new Set(r).add(i))}

            >
              Remove
            </Button>
            )}
          </li>
        ))}
      </ul>
      <p data-testid="totals">
        {SHOWN.map(([n, label, unit]) => {
          const tot = untouched
            ? { lower: t.totals[n]?.known ? t.totals[n]!.lower : null, upper: t.totals[n]?.upper ?? null }
            : ok
              ? scaledTotal(
                  rows.map(({ item, i }) => ({
                    lower: item.nutrients[n]?.known ? item.nutrients[n]!.lower : null,
                    upper: item.nutrients[n]?.upper ?? null,
                    grams: toDec(grams[i]!),
                    baseGrams: item.grams,
                  })),
                )
              : { lower: null, upper: null }
          return <span key={n}>{`${label} ${formatRange(tot.lower, tot.upper, unit)} `}</span>
        })}
      </p>
      {error && <p role="alert">{error}</p>}
      <Button variant="secondary" type="button" disabled={busy || !ok} onClick={add}>
        Add
      </Button>
    </div>
  )
}
