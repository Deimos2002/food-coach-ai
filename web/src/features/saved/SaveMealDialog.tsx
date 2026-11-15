import { Button } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { createSavedMeal } from '../../lib/api'
import type { ItemView, SavedMealIn } from '../../lib/types'

export interface SaveMealDialogProps {
  items: ItemView[]
  onClose: () => void
  onSaved: (savedMealId: number) => void
}

/** Builds a saved meal from a meal's items: product_id, amount and unit, plus a name. */
export function SaveMealDialog({ items, onClose, onSaved }: SaveMealDialogProps) {
  const [name, setName] = useState('')
  const mutation = useMutation({
    mutationFn: (body: SavedMealIn) => createSavedMeal(body),
    onSuccess: (r) => onSaved(r.saved_meal_id),
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const body: SavedMealIn = {
      name,
      items: items.map((it) => ({
        product_id: it.resolved_product_id,
        amount: it.quantity.value,
        unit: it.quantity.unit,
      })),
    }
    mutation.mutate(body)
  }

  return (
    <div className="restyle-dialog ui-card" role="dialog" aria-modal="true" aria-label="Save as meal">
      <form className="restyle-form" onSubmit={submit}>
        <label htmlFor="save-meal-name">Name</label>
        <input id="save-meal-name" value={name} onChange={(e) => setName(e.target.value)} />
        {mutation.error && <p role="alert">{mutation.error.message}</p>}
        <Button type="submit" disabled={mutation.isPending || !name.trim()}>
          Save
        </Button>
        <Button variant="secondary" type="button" onClick={onClose}>
          Cancel
        </Button>
      </form>
    </div>
  )
}
