import { useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ApiError, getDay, getTargets, getWeek } from '../../lib/api'
import { addDays, isoWeekOf, localDay } from '../../lib/dates'
import { formatDec, nutrientDisplayValue, progressAverage } from '../../lib/format'
import { Button, Card, PageHeader } from '../../ui'
import { WeightPanel } from '../weight/WeightPage'
import './ProgressPage.css'

export function ProgressPage({ today = localDay() }: { today?: string } = {}) {
  const [period, setPeriod] = useState<'week' | 'month'>('week')
  const [anchor, setAnchor] = useState(today)
  const date = new Date(`${anchor}T12:00:00`)
  const start = period === 'week' ? addDays(anchor, -((date.getDay() + 6) % 7)) : localDay(new Date(date.getFullYear(), date.getMonth(), 1))
  const count = period === 'week' ? 7 : new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate()
  const days = Array.from({ length: count }, (_, i) => addDays(start, i))
  const weeks = [...new Set(days.map(isoWeekOf))]
  const weekQueries = useQueries({ queries: weeks.map(week => ({ queryKey: ['week', week], queryFn: () => getWeek(week) })) })
  const dayQueries = useQueries({ queries: days.map(day => ({ queryKey: ['day', day], queryFn: () => getDay(day) })) })
  const targets = useQueries({ queries: days.map(day => ({ queryKey: ['targets', day], queryFn: () => getTargets(day), retry: false })) })
  const calories = dayQueries.map(query => query.data?.status === 'complete' ? nutrientDisplayValue(query.data.nutrients.energy_kcal) : null)
  const protein = dayQueries.map((query, i) => query.data?.status === 'complete' && targets[i]?.data ? nutrientDisplayValue(query.data.nutrients.protein) : null)
  const known = protein.filter(value => value !== null).length
  const hits = protein.filter((value, i) => value !== null && value >= (targets[i]?.data?.protein_g ?? Infinity)).length
  const errors = [...weekQueries, ...dayQueries, ...targets].filter(query => query.error && !(query.error instanceof ApiError && query.error.status === 404))
  const loading = [...weekQueries, ...dayQueries, ...targets].some(query => query.isPending)
  function move(n: number) {
    setAnchor(period === 'week' ? addDays(anchor, n * 7) : localDay(new Date(date.getFullYear(), date.getMonth() + n, 1)))
  }
  return <main className="progress-page">
    <PageHeader title="Progress" />
    <div className="progress-controls">
      <div role="group" aria-label="Period">
        <Button variant={period === 'week' ? 'primary' : 'secondary'} aria-pressed={period === 'week'} onClick={() => setPeriod('week')}>Week</Button>
        <Button variant={period === 'month' ? 'primary' : 'secondary'} aria-pressed={period === 'month'} onClick={() => setPeriod('month')}>Month</Button>
      </div>
      <nav aria-label="Progress dates">
        <Button variant="ghost" aria-label={`Previous ${period}`} onClick={() => move(-1)}>{"<"}</Button>
        <span>{period === 'week' ? isoWeekOf(anchor) : date.toLocaleDateString('en', { month: 'long', year: 'numeric' })}</span>
        <Button variant="ghost" aria-label={`Next ${period}`} onClick={() => move(1)}>{">"}</Button>
      </nav>
    </div>
    {loading && <p role="status">Loading progress...</p>}
    {errors.map((query, i) => <p role="alert" key={i}>{query.error?.message}</p>)}
    {targets.some(query => query.error instanceof ApiError && query.error.status === 404) && <Link to="/profile">Set your targets</Link>}
    <Card as="section">
      <h2>Calories</h2>
      <p className="progress-average">Average: {formatDec(progressAverage(calories), 0)} kcal / day</p>
      <div className={`progress-bars progress-bars--${period}`}>
        {days.map((day, i) => {
          const value = calories[i] ?? null
          const target = targets[i]?.data?.kcal ?? null
          return <figure key={day} aria-label={`Calories ${day}`} aria-current={day === today ? 'date' : undefined}>
            <span>{value === null ? '?' : formatDec(String(value), 0)} kcal</span>
            <progress aria-hidden="true" aria-label={`Calories vs target ${day}`} max={target && target > 0 ? target : 1} value={value !== null && target && target > 0 ? Math.min(value, target) : 0} />
            <figcaption>{day.slice(8)}</figcaption>
          </figure>
        })}
      </div>
      <details className="progress-details">
        <summary>Targets &amp; chart details</summary>
        <p className="progress-note">Incomplete or unknown days show ?. Average requires every day to be known.</p>
        <dl className="progress-targets">{days.map((day, i) => <div key={day}><dt><time dateTime={day}>{day}</time></dt><dd>{targets[i]?.data?.kcal ?? '?'} kcal</dd></div>)}</dl>
      </details>
      <div className="progress-protein"><h2>Protein</h2><p>{hits} of {known} known days hit target; {days.length - known} unknown</p></div>
    </Card>
    <Card as="section"><WeightPanel /></Card>
  </main>
}
