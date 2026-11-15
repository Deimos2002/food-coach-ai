import { Card } from '../../ui'
import '../restyle.css'
import { useQuery } from '@tanstack/react-query'
import { ApiError, getTargets } from '../../lib/api'
import { localDay } from '../../lib/dates'
import { formatDec } from '../../lib/format'

export function TargetsCard() {
  const day = localDay()
  const query = useQuery({ queryKey: ['targets', day], queryFn: () => getTargets(day) })
  return <Card as="section" className="restyle-panel">
    <h2>Today's targets</h2>
    {query.isPending ? <p role="status">Loading targets…</p> : query.error ?
      <p role={query.error instanceof ApiError && query.error.status === 404 ? undefined : 'alert'}>
        {query.error instanceof ApiError && query.error.status === 404 ? 'Set your profile and log a weight to get targets' : query.error.message}
      </p> : <dl>
        <dt>Maintenance</dt><dd>{formatDec(String(query.data.maintenance_kcal), 0)} kcal</dd>
        <dt>Target</dt><dd>{formatDec(String(query.data.kcal), 0)} kcal</dd>
        <dt>Protein</dt><dd>{formatDec(String(query.data.protein_g), 0)} g</dd>
      </dl>}
  </Card>
}
