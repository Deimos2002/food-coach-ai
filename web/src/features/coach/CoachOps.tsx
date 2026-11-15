import { useQuery } from '@tanstack/react-query'
import { getCoachOps } from '../../lib/api'
import { localDay } from '../../lib/dates'
import { formatDec, formatShareRange } from '../../lib/format'
import type { Dec } from '../../lib/types'

const rate = (value: Dec | null) => value === null ? '?' : formatShareRange([value, value])
const latency = (value: number | null) => value === null ? '?' : `${formatDec(String(value), 0)} ms`

export function CoachOps({ month = localDay().slice(0, 7) }: { month?: string }) {
  const query = useQuery({ queryKey: ['coach-ops', month], queryFn: () => getCoachOps(month) })
  const data = query.data
  return (
    <section aria-label="Coach ops">
      <h2>Coach ops</h2>
      <p>{month}</p>
      {query.isPending && <p role="status">Loading coach metrics…</p>}
      {query.error && <p role="alert">{query.error.message}</p>}
      {data && <dl>
        <dt>Calls</dt><dd>{data.calls}</dd>
        <dt>Budget</dt><dd>{formatDec(data.spent_eur, 4)} € of {formatDec(data.cap_eur, 4)} €</dd>
        <dt>First-try pass rate</dt><dd>{rate(data.first_try_pass_rate)}</dd>
        <dt>Fallback rate</dt><dd>{rate(data.fallback_rate)}</dd>
        <dt>p50 latency</dt><dd>{latency(data.p50_ms)}</dd>
        <dt>p95 latency</dt><dd>{latency(data.p95_ms)}</dd>
      </dl>}
    </section>
  )
}
