import { PageHeader } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useQuery } from '@tanstack/react-query'
import { listSavedMeals } from '../../lib/api'
import { formatDec } from '../../lib/format'

export function SavedMealsPage() {
  const list = useQuery({ queryKey: ['saved-meals'], queryFn: () => listSavedMeals() })

  return (
    <main className="restyle-page ui-card">
      <PageHeader title="Saved meals" />
      {list.error && <p role="alert">{list.error.message}</p>}
      <ul>
        {(list.data ?? []).map((m) => (
          <li key={m.id}>
            {m.name}
            <ul>
              {m.items.map((it) => (
                <li key={it.product_id}>
                  {it.name} — {formatDec(it.amount)} {it.unit}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </main>
  )
}
