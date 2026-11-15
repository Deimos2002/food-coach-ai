import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { getDay } from '../../lib/api'
import { localDay, addDays } from '../../lib/dates'
import { stats } from '../../lib/trial'

import { Button } from '../../ui/Button'

function formatRun(run: { n: number; medianMs: number | null }): string {
  if (run.medianMs === null) return `no runs yet (n=${run.n})`
  return `${(run.medianMs / 1000).toFixed(1)} s (n=${run.n})`
}

/** The last 14 local days, oldest first, ending today. */
function last14Days(): string[] {
  const today = localDay()
  return Array.from({ length: 14 }, (_, i) => addDays(today, i - 13))
}

export function TrialStats() {
  const [open, setOpen] = useState(false)
  const trial = stats()
  const days = last14Days()
  const queries = useQueries({
    queries: days.map((day) => ({
      queryKey: ['day', day],
      queryFn: () => getDay(day),
      enabled: open,
    })),
  })

  const loaded = queries.filter((q) => q.data !== undefined)
  const completeCount = loaded.filter((q) => q.data?.status === 'complete').length
  const rateText =
    open && loaded.length === days.length
      ? `Complete-day rate (last 14 days): ${completeCount}/${days.length}`
      : open
        ? 'Loading complete-day rate…'
        : null

  return (
    <section aria-label="Usage stats">
      <h2>Usage stats</h2>
      <p>Target: open to successful add in 45 s.</p>
      <ul>
        <li>Cold start: {formatRun(trial.cold)}</li>
        <li>Warm: {formatRun(trial.warm)}</li>
      </ul>
      <Button variant="secondary" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Usage stats
      </Button>
      {open && <p>{rateText}</p>}
    </section>
  )
}
