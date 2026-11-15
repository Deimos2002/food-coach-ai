import { formatRange, scaledTotal } from '../../lib/format'
import { Link } from 'react-router-dom'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import './day.css'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { listSavedMeals, useSavedMeal as applySavedMeal } from '../../lib/api'
import { newKey } from '../../lib/idempotency'
import { AddItemSheet } from '../add/AddItemSheet'
import type { PickedProduct } from '../add/ProductSearch'
import type { QuantityValue } from '../add/QuantityForm'
import { SaveMealDialog } from '../saved/SaveMealDialog'
import type {
  BlockedSavedItem,
  IsoDay,
  ItemView,
  SavedMeal,
  SavedMealItem,
  MealType,
  MealView,
} from '../../lib/types'
import { ItemRow } from './ItemRow'

const LABELS: Record<MealType, string> = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner', snack: 'Snack' }

export interface MealCardProps {
  mealType: MealType
  meal: MealView | undefined
  addLink?: boolean
  busy?: boolean
  onAdd: () => void
  onSetSkipped: (mealId: number, skipped: boolean) => void
  /** Tapping an item row. Omit to render rows without an edit affordance. */
  onEditItem?: (item: ItemView) => void
  /** Needed to invalidate the day query from the "Add saved meal" successor sheet. */
  day?: IsoDay
}

/** The successor to pre-fill the add-item sheet with: the original saved item's amount and unit. */
interface SuccessorPick {
  productId: number
  original: SavedMealItem
}

/** The "Add saved meal" picker: lists saved meals, uses one, and for a blocked item with
 * successors opens the add-item sheet (Task 4) pre-filled with the original amount and unit so
 * the user confirms or adjusts before anything is logged — never auto-adds a placeholder. */
function UseSavedMealPanel({ mealId, day, onClose }: { mealId: number; day: IsoDay; onClose: () => void }) {
  const qc = useQueryClient()
  const list = useQuery({ queryKey: ['saved-meals'], queryFn: () => listSavedMeals() })
  const [activeMeal, setActiveMeal] = useState<SavedMeal | null>(null)
  const [blocked, setBlocked] = useState<BlockedSavedItem[]>([])
  const [added, setAdded] = useState<number[] | null>(null)
  const [successor, setSuccessor] = useState<SuccessorPick | null>(null)
  const use = useMutation({
    mutationFn: (savedMeal: SavedMeal) =>
      applySavedMeal(savedMeal.id, mealId, newKey()).then((r) => ({ savedMeal, r })),
    onSuccess: async ({ savedMeal, r }) => {
      setActiveMeal(savedMeal)
      setBlocked(r.blocked)
      setAdded(r.added)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['day', day] }),
        qc.invalidateQueries({ queryKey: ['recent'] }),
      ])
    },
  })

  const pickSuccessor = (item: BlockedSavedItem, productId: number) => {
    const original = activeMeal?.items.find((i) => i.product_id === item.product_id)
    if (!original) return
    setSuccessor({ productId, original })
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Add saved meal">
      {list.error && <p role="alert">{list.error.message}</p>}
      {use.error && <p role="alert">{use.error.message}</p>}
      <ul aria-label="Saved meals">
        {(list.data ?? []).map((m) => (
          <li key={m.id}>
            <Button variant="ghost" type="button" onClick={() => use.mutate(m)} disabled={use.isPending}>
              {m.name}
            </Button>
          </li>
        ))}
      </ul>
      {added !== null && added.length > 0 && (
        <p>{`Added ${added.length} item${added.length === 1 ? '' : 's'}`}</p>
      )}
      {blocked.length > 0 && (
        <div>
          <p>Replaced — choose:</p>
          {blocked.map((item) => (
            <div key={item.product_id}>
              {item.successors.map((id) => (
                <Button variant="ghost" key={id} type="button" onClick={() => pickSuccessor(item, id)}>
                  {`Use product #${id}`}
                </Button>
              ))}
            </div>
          ))}
        </div>
      )}
      <Button variant="ghost" type="button" onClick={onClose}>
        Close
      </Button>
      {successor && (
        <AddItemSheet
          mealId={mealId}
          day={day}
          initialProduct={
            {
              product_id: successor.productId,
              name: `Product #${successor.productId}`,
              basis_unit: successor.original.unit,
              portions: [],
            } satisfies PickedProduct
          }
          initialQuantity={
            {
              amount: successor.original.amount,
              unit: successor.original.unit,
              source: 'user_estimate',
              range: false,
              lower: '',
              upper: '',
            } satisfies QuantityValue
          }
          onClose={() => {
            setBlocked((b) => b.filter((x) => x.product_id !== successor.original.product_id))
            setSuccessor(null)
          }}
        />
      )}
    </div>
  )
}

/** Items sharing a dish become one group at the position of the first; others stay single. */
function groupByDish(items: ItemView[]): { dish: string | null; items: ItemView[] }[] {
  const groups: { dish: string | null; items: ItemView[] }[] = []
  for (const it of items) {
    const g = it.dish ? groups.find((x) => x.dish === it.dish) : undefined
    if (g) g.items.push(it)
    else groups.push({ dish: it.dish || null, items: [it] })
  }
  return groups
}

export function MealCard({ mealType, meal, busy = false, onAdd, onSetSkipped, onEditItem, day, addLink = true }: MealCardProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [dialog, setDialog] = useState<'save' | 'use' | null>(null)

  const energy = meal && meal.items.length ? scaledTotal(meal.items.map((item) => ({
    lower: item.nutrients.energy_kcal?.known ? item.nutrients.energy_kcal.lower : null,
    upper: item.nutrients.energy_kcal?.known ? item.nutrients.energy_kcal.upper : null,
    grams: '1', baseGrams: '1',
  }))) : null
  return (
    <Card as="section" className="meal-card"><div data-testid={`meal-${mealType}`}>
      <h3>{LABELS[mealType]}</h3>
      <p className="meal-energy" aria-label="Meal energy">Recorded: {energy ? formatRange(energy.lower, energy.upper, 'kcal') : '?'}</p>
      {meal?.skipped && <p>Skipped</p>}
      {meal && meal.items.length > 0 && (
        <ul>
          {groupByDish(meal.items).map((g) =>
            g.dish === null ? (
              <ItemRow key={g.items[0]!.item_id} item={g.items[0]!} onEdit={onEditItem} />
            ) : (
              <li key={`dish-${g.items[0]!.item_id}`}>
                <h4>{g.dish}</h4>
                <ul>
                  {g.items.map((it) => (
                    <ItemRow key={it.item_id} item={it} onEdit={onEditItem} />
                  ))}
                </ul>
              </li>
            ),
          )}
        </ul>
      )}
      {meal && day && addLink ? <Link className="meal-add" aria-label={`Add to ${LABELS[mealType]}`} aria-disabled={busy} to={`/add/${day}/${meal.meal_id}`} onClick={(e) => {
        e.preventDefault()
        if (!busy) onAdd()
      }}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></Link> : <Button className="meal-add" variant="secondary" onClick={onAdd} disabled={busy} aria-label="Add"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></Button>}
      {meal && (
        <>
          <Button variant="ghost" type="button" onClick={() => onSetSkipped(meal.meal_id, !meal.skipped)} disabled={busy}>
            {meal.skipped ? 'Unmark skipped' : 'Mark skipped'}
          </Button>
          <Button variant="ghost" type="button" aria-label="Meal options" onClick={() => setMenuOpen((o) => !o)}>
            ⋮
          </Button>
          {menuOpen && (
            <div role="menu">
              <Button variant="ghost" type="button" onClick={() => setDialog('save')}>
                Save as meal
              </Button>
              <Button variant="ghost" type="button" onClick={() => setDialog('use')}>
                Add saved meal
              </Button>
            </div>
          )}
          {dialog === 'save' && <SaveMealDialog items={meal.items} onClose={() => setDialog(null)} onSaved={() => setDialog(null)} />}
          {dialog === 'use' && (
            <UseSavedMealPanel mealId={meal.meal_id} day={day ?? ''} onClose={() => setDialog(null)} />
          )}
        </>
      )}
    </div></Card>
  )
}
