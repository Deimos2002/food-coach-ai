import { Button } from '../../ui/Button'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { addItem, ApiError, getProduct, getDay, useSavedMeal as applySavedMeal } from '../../lib/api'
import { newKey } from '../../lib/idempotency'
import { startTimer, stopTimer } from '../../lib/trial'
import type { AddItemPayload, Dec, IsoDay, Unit } from '../../lib/types'
import { ProductSearch, type PickedProduct } from './ProductSearch'
import { MealTemplateSheet } from './MealTemplateSheet'
import { AddedTray, type AddedItem } from './AddedTray'
import './AddPage.css'
import { QuantityForm, type QuantityValue } from './QuantityForm'

export interface AddPageProps {
  mealId: number
  day: IsoDay
  onClose: () => void
  onAdded?: () => void
  /** Pre-fills the product and skips search (e.g. a saved-meal successor pick). Additive, optional. */
  initialProduct?: PickedProduct
  /** Pre-fills the quantity form when `initialProduct` is set. */
  initialQuantity?: QuantityValue
}

/** The only normalisation: a decimal comma becomes a dot. The string is otherwise sent as typed. */
const toDecString = (s: string): Dec => s.trim().replaceAll(',', '.')

/** A successor's name, fetched by id. Falls back to "#id" while loading, or on error. */
function SuccessorButton({ id, onPick }: { id: number; onPick: (id: number) => void }) {
  const q = useQuery({ queryKey: ['product', id], queryFn: () => getProduct(id), retry: false })
  const label = q.data?.name ?? `#${id}`
  return (
    <Button variant="secondary" type="button" onClick={() => onPick(id)} >
      {`Use ${label}`}
    </Button>
  )
}

function blankQuantity(unit: Unit, amount = ''): QuantityValue {
  return { amount, unit, source: 'user_estimate', range: false, lower: '', upper: '' }
}

function successorsOf(err: unknown): number[] {
  if (!(err instanceof ApiError) || err.status !== 422) return []
  const s = (err.body as { successors?: unknown } | null)?.successors
  return Array.isArray(s) ? s.filter((x): x is number => typeof x === 'number') : []
}

export function AddPage({ mealId, day, onClose, initialProduct, initialQuantity, onAdded }: AddPageProps) {
  const qc = useQueryClient()
  const [added, setAdded] = useState<AddedItem[]>([])
  const [timerStart] = useState(startTimer)
  const [product, setProduct] = useState<PickedProduct | null>(initialProduct ?? null)
  const [qty, setQty] = useState<QuantityValue>(() => initialQuantity ?? blankQuantity('g'))
  // One key per user action: new on pick, and new whenever the product or quantity changes.
  // Network retries inside addItem reuse it, so a flaky connection still adds one item.
  const [key, setKey] = useState<string | null>(() => (initialProduct ? newKey() : null))
  const [templateId, setTemplateId] = useState<number | null>(null)
  const inFlight = useRef(false)
  const submittedName = useRef('')

  const mutation = useMutation({
    mutationFn: ({ payload, k }: { payload: AddItemPayload; k: string }) => addItem(mealId, payload, k),
    onSuccess: async (result) => {
      setAdded((items) => [...items, { ...result, name: submittedName.current }])
      stopTimer(timerStart)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['day', day] }),
        qc.invalidateQueries({ queryKey: ['recent'] }),
      ])
      onAdded?.()
      setProduct(null)
      setKey(null)
      mutation.reset()
    },
    onSettled: () => {
      inFlight.current = false
    },
  })

  const pick = (p: PickedProduct) => {
    setProduct(p)
    setQty(blankQuantity(p.last_unit ?? p.basis_unit, p.last_amount ?? ''))
    setKey(newKey())
    mutation.reset()
  }

  const changeQty = (v: QuantityValue) => {
    setQty(v)
    setKey(newKey())
  }

  /**
   * Picking a successor resets the unit to its basis unit. When that isn't already known,
   * it's fetched with getProduct; if it still can't be determined, the unit is left empty
   * and the user must choose.
   */
  const switchTo = async (productId: number) => {
    if (!product) return
    setKey(newKey())
    mutation.reset()
    try {
      const p = await getProduct(productId)
      setProduct({ ...product, product_id: productId, name: p.name, portions: p.portions, basis_unit: p.basis_unit })
      setQty((q) => ({ ...q, unit: p.basis_unit }))
    } catch {
      setProduct({ ...product, product_id: productId, name: `Product #${productId}`, portions: [] })
      setQty((q) => ({ ...q, unit: '' }))
    }
  }

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!product || !key || !qty.unit || inFlight.current) return
    inFlight.current = true
    submittedName.current = product.name
    const payload: AddItemPayload = {
      product_id: product.product_id,
      amount: toDecString(qty.amount),
      unit: qty.unit,
      quantity_source: qty.source,
    }
    if (qty.range) {
      payload.lower = qty.lower.trim() ? toDecString(qty.lower) : null
      payload.upper = qty.upper.trim() ? toDecString(qty.upper) : null
    }
    mutation.mutate({ payload, k: key })
  }

  const savedAction = useRef<{ id: number; key: string } | null>(null)
  const savedInFlight = useRef(false)
  const saved = useMutation({ mutationFn: async (id: number) => {
    const result = await applySavedMeal(id, mealId, savedAction.current!.key)
    setAdded((items) => [...items, ...result.added.filter((id) => !items.some((item) => item.item_id === id)).map((item_id) => ({ item_id, name: 'Saved meal ingredient', nutrients: {} }))])
    try {
      const view = await getDay(day)
      const details = view.meals.flatMap((meal) => meal.items)
      setAdded((items) => items.map((item) => details.find((detail) => detail.item_id === item.item_id) ?? item))
    } catch {
      // The POST succeeded: keep undoable items with unknown totals if the detail refresh fails.
    }
    await Promise.all([qc.invalidateQueries({ queryKey: ['day', day] }), qc.invalidateQueries({ queryKey: ['recent'] })])
    if (result.blocked.length) throw new Error('Some saved ingredients were replaced. Use Saved meals on Today to choose successors.')
    savedAction.current = null
  }, onSettled: () => { savedInFlight.current = false } })
  const addSaved = (id: number) => {
    if (savedInFlight.current) return
    savedInFlight.current = true
    if (savedAction.current?.id !== id) savedAction.current = { id, key: newKey() }
    saved.mutate(id)
  }
  const quickAdd = (p: PickedProduct, amount: Dec, source: 'label_portion' | 'user_estimate') => {
    if (inFlight.current) return
    inFlight.current = true
    setProduct(p)
    const actionKey = newKey()
    setKey(actionKey)
    setQty({ ...blankQuantity(p.basis_unit, amount), source })
    submittedName.current = p.name
    mutation.mutate({ payload: { product_id: p.product_id, amount, unit: p.basis_unit, quantity_source: source }, k: actionKey })
  }
  const successors = successorsOf(mutation.error)
  const busy = mutation.isPending

  return (
    <main className="add-page" aria-label="Add food">
      <header>
        <h1>Add food</h1>
        <Button variant="secondary" type="button" onClick={onClose}>
          Done
        </Button>
      </header>
      {product === null && (mutation.error || saved.error) && <p role="alert">{(mutation.error ?? saved.error)?.message}</p>}
      {templateId !== null ? <MealTemplateSheet panel templateId={templateId} mealId={mealId} day={day} onBack={() => setTemplateId(null)} onClose={() => setTemplateId(null)} onAdded={(item) => setAdded((items) => [...items, item])} /> : product === null ? (
        <ProductSearch onPick={pick} onPickMeal={setTemplateId} mealId={mealId} day={day} onQuickAdd={quickAdd} onPickSaved={addSaved} busy={busy || saved.isPending} />
      ) : (
        <form onSubmit={submit}>
          <p>
            <strong>{product.name}</strong>{' '}
            <Button variant="secondary" type="button" disabled={busy} onClick={() => setProduct(null)}>
              Change
            </Button>
          </p>
          <QuantityForm value={qty} portions={product.portions} basisUnit={product.basis_unit} onChange={changeQty} />
          {mutation.error &&
            (successors.length > 0 ? (
              <div role="alert">
                <p>This product was replaced</p>
                {successors.map((id) => (
                  <SuccessorButton key={id} id={id} onPick={switchTo} />
                ))}
              </div>
            ) : (
              <p role="alert">{mutation.error.message}</p>
            ))}
          <Button variant="primary" type="submit" disabled={busy}>
            Add
          </Button>
        </form>
      )}
      <AddedTray items={added} day={day} onUndo={(id) => setAdded((items) => items.filter((item) => item.item_id !== id))} />
    </main>
  )
}
