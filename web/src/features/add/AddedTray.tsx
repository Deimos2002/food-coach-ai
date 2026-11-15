import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteItem } from '../../lib/api'
import { formatRange, scaledTotal } from '../../lib/format'
import type { ItemResult } from '../../lib/types'
import { Button } from '../../ui/Button'
export type AddedItem = ItemResult & { name: string }
export function AddedTray({ items, day, onUndo }: { items: AddedItem[]; day: string; onUndo: (id: number) => void }) {
  const qc = useQueryClient()
  const undo = useMutation({ mutationFn: deleteItem, onSuccess: async (_, id) => {
    onUndo(id)
    await Promise.all([qc.invalidateQueries({ queryKey: ['day', day] }), qc.invalidateQueries({ queryKey: ['recent'] })])
  } })
  const total = scaledTotal(items.map((item) => ({ lower: item.nutrients.energy_kcal?.known ? item.nutrients.energy_kcal.lower : null, upper: item.nutrients.energy_kcal?.known ? item.nutrients.energy_kcal.upper : null, grams: '1', baseGrams: '1' })))
  return <section className="added-tray" aria-label="Added">
    <h2>Added</h2><p aria-label="Added total">{items.length ? formatRange(total.lower, total.upper, 'kcal') : 'No items yet'}</p>
    {!items.length && <p>Your additions will appear here.</p>}
    <ul>{items.map((item) => <li key={item.item_id}><span>{item.name}</span><Button variant="ghost" disabled={undo.isPending} onClick={() => undo.mutate(item.item_id)} aria-label={`Undo ${item.name}`}>Undo</Button></li>)}</ul>
    {undo.error && <p role="alert">{undo.error.message}</p>}
  </section>
}
