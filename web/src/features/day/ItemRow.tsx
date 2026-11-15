import { Button } from '../../ui/Button'
import { formatQuantity, formatRange } from '../../lib/format'
import type { ItemView } from '../../lib/types'

export interface ItemRowProps {
  item: ItemView
  onEdit?: (item: ItemView) => void
}

export function ItemRow({ item, onEdit }: ItemRowProps) {
  const e = item.nutrients.energy_kcal
  const energy = e ? formatRange(e.lower, e.upper, 'kcal') : '?'
  const merged = item.requested_product_id !== item.resolved_product_id
  return (
    <li className="item-row">
      <span>{item.name}</span> <span>{formatQuantity(item.quantity)}</span> <span>{energy}</span>
      {merged && <span role="status">product since merged / updated</span>}
      {onEdit && (
        <Button variant="ghost" type="button" onClick={() => onEdit(item)}>
          Edit
        </Button>
      )}
    </li>
  )
}
