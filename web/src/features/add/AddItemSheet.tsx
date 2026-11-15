import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { addItem, ApiError, getProduct } from '../../lib/api'
import { newKey } from '../../lib/idempotency'
import { startTimer, stopTimer } from '../../lib/trial'
import type { AddItemPayload, Dec, IsoDay, Unit } from '../../lib/types'
import { ProductSearch, type PickedProduct } from './ProductSearch'
import { MealTemplateSheet } from './MealTemplateSheet'
import { QuantityForm, type QuantityValue } from './QuantityForm'

export interface AddItemSheetProps {
  mealId: number
  day: IsoDay
  onClose: () => void
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
    <button type="button" onClick={() => onPick(id)} style={{ minHeight: 44 }}>
      {`Use ${label}`}
    </button>
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

const SHEET_CSS = `
.add-sheet { position: fixed; inset: 0; background: Canvas; color: CanvasText; overflow-y: auto;
  padding: 16px; box-sizing: border-box; z-index: 10; }
@media (min-width: 640px) {
  .add-sheet { inset: 10vh auto auto 50%; transform: translateX(-50%); width: 560px; max-height: 80vh;
    border: 1px solid GrayText; border-radius: 8px; }
}
`

export function AddItemSheet({ mealId, day, onClose, initialProduct, initialQuantity }: AddItemSheetProps) {
  const qc = useQueryClient()
  const [timerStart] = useState(startTimer)
  const [product, setProduct] = useState<PickedProduct | null>(initialProduct ?? null)
  const [qty, setQty] = useState<QuantityValue>(() => initialQuantity ?? blankQuantity('g'))
  // One key per user action: new on pick, and new whenever the product or quantity changes.
  // Network retries inside addItem reuse it, so a flaky connection still adds one item.
  const [key, setKey] = useState<string | null>(() => (initialProduct ? newKey() : null))
  const [templateId, setTemplateId] = useState<number | null>(null)
  const inFlight = useRef(false)

  const mutation = useMutation({
    mutationFn: ({ payload, k }: { payload: AddItemPayload; k: string }) => addItem(mealId, payload, k),
    onSuccess: async () => {
      stopTimer(timerStart)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['day', day] }),
        qc.invalidateQueries({ queryKey: ['recent'] }),
      ])
      onClose()
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

  const successors = successorsOf(mutation.error)
  const busy = mutation.isPending || mutation.isSuccess

  if (templateId !== null) {
    return (
      <MealTemplateSheet
        templateId={templateId}
        mealId={mealId}
        day={day}
        onClose={onClose}
        onBack={() => setTemplateId(null)}
      />
    )
  }

  return (
    <div className="add-sheet" role="dialog" aria-modal="true" aria-label="Add item">
      <style>{SHEET_CSS}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Add item</h2>
        <button type="button" onClick={onClose} style={{ minHeight: 44, minWidth: 44 }}>
          Close
        </button>
      </div>
      {product === null ? (
        <ProductSearch onPick={pick} onPickMeal={setTemplateId} mealId={mealId} day={day} />
      ) : (
        <form onSubmit={submit}>
          <p>
            <strong>{product.name}</strong>{' '}
            <button type="button" disabled={busy} onClick={() => setProduct(null)} style={{ minHeight: 44 }}>
              Change
            </button>
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
          <button type="submit" disabled={busy} style={{ minHeight: 44, width: '100%' }}>
            Add
          </button>
        </form>
      )}
    </div>
  )
}
