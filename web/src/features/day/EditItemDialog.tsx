import { Button } from '../../ui/Button'
import './day.css'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { createMeal, deleteItem, getDay, moveItem, patchItem } from '../../lib/api'
import { QuantityForm, type QuantityValue } from '../add/QuantityForm'
import type { IsoDay, ItemView, MealType, PatchItemPayload, Unit } from '../../lib/types'

export interface EditItemDialogProps {
  item: ItemView
  day: IsoDay
  onClose: () => void
}

const MEAL_TYPES: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack']

/** The only normalisation: a decimal comma becomes a dot. The string is otherwise sent as typed. */
const toDec = (s: string): string => s.trim().replaceAll(',', '.')

/**
 * Normalises a decimal string for comparison only (never for sending): strips trailing zeros
 * after a decimal point, then a trailing bare point, so "210.0" reads as unchanged from "210".
 * String manipulation only — no Number()/parseFloat.
 */
function normDec(s: string): string {
  const v = toDec(s)
  if (!v.includes('.')) return v
  return v.replace(/0+$/, '').replace(/\.$/, '')
}

const normDecOrNull = (s: string | null): string | null => (s === null ? null : normDec(s))

function initialQty(item: ItemView): QuantityValue {
  return {
    amount: item.quantity.value,
    unit: item.quantity.unit,
    source: item.quantity.source,
    range: item.quantity.kind === 'estimated_range',
    lower: item.quantity.lower ?? '',
    upper: item.quantity.upper ?? '',
  }
}

export function EditItemDialog({ item, day, onClose }: EditItemDialogProps) {
  const qc = useQueryClient()
  const [qty, setQty] = useState<QuantityValue>(() => initialQty(item))
  const [targetDay, setTargetDay] = useState<IsoDay>(day)
  const [mealType, setMealType] = useState<MealType>('breakfast')
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  // Same query key as DayPage's own query: react-query shares the in-flight request and cache,
  // and a short staleTime stops this mount from immediately refetching data DayPage just loaded.
  const dayQuery = useQuery({ queryKey: ['day', day], queryFn: () => getDay(day), staleTime: 10_000 })
  const targetQuery = useQuery({
    queryKey: ['day', targetDay],
    queryFn: () => getDay(targetDay),
    enabled: targetDay !== day,
  })
  const targetMeals = targetDay === day ? dayQuery.data?.meals : targetQuery.data?.meals

  const invalidate = (otherDay?: IsoDay) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ['day', day] }),
      otherDay && otherDay !== day ? qc.invalidateQueries({ queryKey: ['day', otherDay] }) : Promise.resolve(),
    ])

  const editMutation = useMutation({
    mutationFn: (payload: PatchItemPayload) => patchItem(item.item_id, payload),
    onSuccess: async () => {
      await invalidate()
      onClose()
    },
  })

  const moveMutation = useMutation({
    mutationFn: async () => {
      const existing = targetMeals?.find((m) => m.meal_type === mealType)
      const mealId = existing ? existing.meal_id : (await createMeal(targetDay, mealType)).meal_id
      return moveItem(item.item_id, mealId)
    },
    onSuccess: async () => {
      await invalidate(targetDay)
      onClose()
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteItem(item.item_id),
    onSuccess: async () => {
      await invalidate()
      onClose()
    },
  })

  const submitEdit = (e: React.FormEvent) => {
    e.preventDefault()
    const patch: PatchItemPayload = {}
    if (normDec(qty.amount) !== normDec(item.quantity.value)) patch.amount = toDec(qty.amount)
    if (qty.unit && qty.unit !== item.quantity.unit) patch.unit = qty.unit
    if (qty.source !== item.quantity.source) patch.quantity_source = qty.source
    if (qty.range) {
      const lower = qty.lower.trim() ? toDec(qty.lower) : null
      const upper = qty.upper.trim() ? toDec(qty.upper) : null
      if (normDecOrNull(lower) !== normDecOrNull(item.quantity.lower)) patch.lower = lower
      if (normDecOrNull(upper) !== normDecOrNull(item.quantity.upper)) patch.upper = upper
    }
    if (Object.keys(patch).length === 0) return
    editMutation.mutate(patch)
  }

  const makeExact = () => {
    const patch: PatchItemPayload = { lower: null, upper: null }
    if (normDec(qty.amount) !== normDec(item.quantity.value)) patch.amount = toDec(qty.amount)
    setQty((q) => ({ ...q, range: false, lower: '', upper: '' }))
    editMutation.mutate(patch)
  }

  const submitMove = (e: React.FormEvent) => {
    e.preventDefault()
    moveMutation.mutate()
  }

  const error = editMutation.error ?? moveMutation.error ?? deleteMutation.error
  const isComplete = dayQuery.data?.status === 'complete'

  return (
    <div className="edit-item-dialog" role="dialog" aria-modal="true" aria-label="Edit item">
      <h2>Edit {item.name}</h2>
      {error && <p role="alert">{error.message}</p>}

      <form onSubmit={submitEdit}>
        <QuantityForm value={qty} portions={[]} basisUnit={(qty.unit || item.quantity.unit) as Unit} onChange={setQty} />
        {item.quantity.kind === 'estimated_range' && (
          <Button variant="secondary" type="button" onClick={makeExact} disabled={editMutation.isPending}>
            Make exact
          </Button>
        )}
        <Button variant="secondary" type="submit" disabled={editMutation.isPending}>
          Save
        </Button>
      </form>

      <form onSubmit={submitMove}>
        <h3>Move</h3>
        <label>
          Date
          <input type="date" value={targetDay} onChange={(e) => setTargetDay(e.target.value)} />
        </label>
        <label>
          Meal
          <select value={mealType} onChange={(e) => setMealType(e.target.value as MealType)}>
            {MEAL_TYPES.map((mt) => (
              <option key={mt} value={mt}>
                {mt}
              </option>
            ))}
          </select>
        </label>
        <Button variant="secondary" type="submit" disabled={moveMutation.isPending}>
          Move
        </Button>
      </form>

      <div>
        {!confirmingDelete ? (
          <Button variant="secondary" type="button" onClick={() => setConfirmingDelete(true)}>
            Delete
          </Button>
        ) : (
          <div role="alertdialog">
            <p>
              Delete this item?
              {isComplete && ' The day will be marked partial.'}
            </p>
            <Button variant="secondary" type="button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              Confirm delete
            </Button>
            <Button variant="secondary" type="button" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
          </div>
        )}
      </div>

      <Button variant="secondary" type="button" onClick={onClose}>
        Close
      </Button>
    </div>
  )
}
