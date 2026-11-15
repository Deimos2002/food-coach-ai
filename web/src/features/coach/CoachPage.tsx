import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getWeek } from '../../lib/api'
import { addWeeks, isoWeekOf, localDay } from '../../lib/dates'
import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import { PageHeader } from '../../ui/PageHeader'
import { CoachCard } from './CoachCard'
import './CoachPage.css'

export function CoachPage() {
  const [isoWeek, setIsoWeek] = useState(() => isoWeekOf(localDay()))
  const query = useQuery({ queryKey: ['week', isoWeek], queryFn: () => getWeek(isoWeek), retry: false })
  return <main className="coach-page">
    <PageHeader title="Coach" actions={<nav className="coach-week-nav" aria-label="Coach week">
      <Button variant="ghost" aria-label="Previous week" onClick={() => setIsoWeek(addWeeks(isoWeek, -1))}>‹</Button>
      <span aria-live="polite">{isoWeek}</span>
      <Button variant="ghost" aria-label="Next week" onClick={() => setIsoWeek(addWeeks(isoWeek, 1))}>›</Button>
    </nav>} />
    {query.isPending && <Card><p role="status">Loading week…</p></Card>}
    {query.error && <Card>
      <p role="alert">{query.error.message}</p>
      <Button variant="secondary" onClick={() => void query.refetch()}>Retry loading week</Button>
    </Card>}
    {query.data && <CoachCard isoWeek={isoWeek} completeDays={query.data.complete_days} />}
  </main>
}
