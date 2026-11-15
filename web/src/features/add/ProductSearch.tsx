import { Button } from '../../ui/Button'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { recentItems, search, listSavedMeals } from '../../lib/api'
import { Tabs } from '../../ui/Tabs'
import { formatDec, formatRange, scaledTotal } from '../../lib/format'
import type { Dec, IsoDay, Portion, Unit } from '../../lib/types'

/** What the quantity form needs about the picked product. */
export interface PickedProduct {
  product_id: number
  name: string
  basis_unit: Unit
  portions: Portion[]
  /** Prefill from a recent item, if any. */
  last_amount?: Dec
  last_unit?: Unit
}

function portionEnergy(kcal: Dec | null, amount: Dec) {
  const total = scaledTotal([{ lower: kcal, upper: kcal, grams: amount, baseGrams: '100' }])
  return formatRange(total.lower, total.upper, 'kcal')
}

const DEBOUNCE_MS = 200

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

const shownName = (r: { name: string; name_en?: string | null }) => r.name_en ?? r.name

/** The French name, shown small beside an English display name. */
const FrenchName = ({ r }: { r: { name: string; name_en?: string | null } }) =>
  r.name_en && r.name_en !== r.name ? <small> ({r.name})</small> : null

export function ProductSearch({
  onPick,
  onPickMeal,
  mealId,
  day,
  onQuickAdd,
  onPickSaved,
  busy,
}: {
  onPick: (p: PickedProduct) => void
  /** A meal-template result was picked. */
  onPickMeal?: (templateId: number) => void
  /** When known, carried on the "Create product" link so a successful create can return here. */
  mealId?: number
  onQuickAdd?: (p: PickedProduct, amount: Dec, source: 'label_portion' | 'user_estimate') => void
  onPickSaved?: (id: number) => void
  busy?: boolean
  day?: IsoDay
}) {
  const [tab, setTab] = useState('Recent')
  const [portions, setPortions] = useState<Record<number, { amount: Dec; source: 'label_portion' | 'user_estimate' }>>({})
  const saved = useQuery({ queryKey: ['saved-meals'], queryFn: listSavedMeals, enabled: tab === 'Saved' })
  const [q, setQ] = useState('')
  const term = useDebounced(q.trim(), DEBOUNCE_MS)
  const short = q.trim().length < 2
  const recent = useQuery({ queryKey: ['recent'], queryFn: () => recentItems() })
  const results = useQuery({
    queryKey: ['search', term],
    queryFn: () => search(term),
    enabled: term.length >= 2,
  })
  const error = tab === 'Saved' ? saved.error : short && tab === 'Recent' ? recent.error : results.error

  const hits = (results.data ?? []).filter((r) => {
    if (tab === 'Dishes') return r.type === 'meal'
    if (tab === 'Fast food') return r.type === 'meal' ? r.chain !== null : (r.is_fast_food ?? r.brand !== null)
    return true
  })
  const savedMeals = (saved.data ?? []).filter((meal) => meal.name.toLowerCase().includes(q.trim().toLowerCase()))

  return (
    <div className="product-search">
      <input
        type="search"
        aria-label="Search food"
        placeholder="Search food"
        value={q}
        autoFocus
        onChange={(e) => setQ(e.target.value)}

      />
      {error && <p role="alert">{error.message}</p>}
      <Tabs items={['Recent', 'Dishes', 'Fast food', ...(onPickSaved ? ['Saved'] : [])].map((label) => ({ id: label, label }))} value={tab} onChange={setTab} />
      {(recent.isPending && short && tab === 'Recent' || results.isFetching && !short || saved.isPending && tab === 'Saved') && <p role="status">Loading food...</p>}
      {tab === 'Saved' && !saved.isPending && !error && !savedMeals.length && <p>No saved meals found.</p>}
      {short && tab !== 'Saved' && tab !== 'Recent' && <p>Search for a dish or restaurant above.</p>}
      {!short && tab !== 'Saved' && !results.isFetching && !error && !hits.length && <p>No matches. Try another name or create a product.</p>}
      {short && tab === 'Recent' && !recent.isPending && !error && !recent.data?.length && <p>Search above to add your first food.</p>}
      {tab === 'Saved' ? <ul aria-label="Saved meals">{savedMeals.map((meal) => <li key={meal.id}><Button variant="secondary" type="button" disabled={busy} onClick={() => onPickSaved?.(meal.id)}>{meal.name}</Button></li>)}</ul> : short && tab === 'Recent' ? (
        <ul aria-label="Recent items">
          {(recent.data ?? []).map((r) => (
            <li key={r.product_id}>
              <Button variant="secondary"
                type="button"

                onClick={() =>
                  onPick({
                    product_id: r.product_id,
                    name: r.name,
                    basis_unit: r.basis_unit,
                    portions: [],
                    last_amount: r.last_amount,
                    last_unit: r.last_unit,
                  })
                }
              >
                {r.name}
                {r.brand && ` · ${r.brand}`} · {formatDec(r.last_amount)} {r.last_unit}
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <ul aria-label="Search results">
          {hits.map((r) =>
            r.type === 'meal' ? (
              <li key={`meal-${r.template_id}`}>
                <Button variant="secondary"
                  type="button"

                  onClick={() => onPickMeal?.(r.template_id)}
                >
                  <strong>{shownName(r)}</strong>
                  <FrenchName r={r} /> <span className="chip">Dish</span>
                  {r.chain && <span className="chip">{r.chain}</span>}
                  {r.estimate && <span className="chip">Estimate</span>}
                </Button>
              </li>
            ) : (
              <li key={r.product_id}>
                <Button variant="secondary"
                  type="button"

                  onClick={() =>
                    onPick({ product_id: r.product_id, name: shownName(r), basis_unit: r.basis_unit, portions: r.portions })
                  }
                >
                  <strong>{shownName(r)}</strong>
                  <FrenchName r={r} /> {r.brand && <span>{r.brand}</span>}{' '}
                  <span>
                    {r.kcal_per_100 === null ? '?' : formatDec(r.kcal_per_100, 0)} kcal / 100 {r.basis_unit}
                  </span>
                </Button>
                {r.portions.length > 0 && (
                  <span>
                    {r.portions.map((p) => (
                      onQuickAdd ? <Button variant="secondary" type="button" key={p.label} aria-pressed={portions[r.product_id]?.source === 'label_portion' && portions[r.product_id]?.amount === p.amount} onClick={() => setPortions((values) => ({ ...values, [r.product_id]: { amount: p.amount, source: 'label_portion' } }))}>{p.label}</Button> : <span key={p.label} className="chip">{p.label}</span>
                    ))}
                  </span>
                )}
                {onQuickAdd && <>
                  <span className="portion-energy">{portionEnergy(r.kcal_per_100, portions[r.product_id]?.amount ?? '100')} / portion</span>
                  <Button variant="secondary" type="button" aria-pressed={!portions[r.product_id] || portions[r.product_id]?.source === 'user_estimate'} onClick={() => setPortions((values) => ({ ...values, [r.product_id]: { amount: '100', source: 'user_estimate' } }))}>100 {r.basis_unit}</Button>
                  <Button variant="secondary" type="button" aria-label={`Add ${shownName(r)}`} disabled={busy} onClick={() => onQuickAdd({ product_id: r.product_id, name: shownName(r), basis_unit: r.basis_unit, portions: r.portions }, portions[r.product_id]?.amount ?? '100', portions[r.product_id]?.source ?? 'user_estimate')}>+</Button>
                </>}
              </li>
            ),
          )}
        </ul>
      )}
      <Link
        to={
          mealId !== undefined && day
            ? `/products/new?meal=${mealId}&day=${day}`
            : '/products/new'
        }
      >
        Not found? Create product
      </Link>
    </div>
  )
}
