import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ApiError, putWeight, weightTrend } from '../../lib/api'
import { localDay } from '../../lib/dates'
import { TrendList } from './TrendList'
import { TargetsCard } from '../profile/TargetsCard'
import { Button } from '../../ui/Button'
import { formatDec, trendChange } from '../../lib/format'
import '../progress/ProgressPage.css'


export function WeightPanel() {
  const cache = useQueryClient()
  const [kg, setKg] = useState('')
  const [replacement, setReplacement] = useState<{ day: string; kg: string } | null>(null)
  const [saved, setSaved] = useState(false)
  const day = localDay()
  const trend = useQuery({ queryKey: ['weight-trend'], queryFn: weightTrend })
  const mutation = useMutation({
    mutationFn: ({ day, kg, replace = false }: { day: string; kg: string; replace?: boolean }) => putWeight(day, kg, replace),
    onSuccess: async () => {
      setReplacement(null)
      setSaved(true)
      await Promise.all([
        cache.invalidateQueries({ queryKey: ['weight-trend'] }),
        cache.invalidateQueries({ queryKey: ['targets'] }),
      ])
    },
    onError: (error, variables) => {
      if (error instanceof ApiError && error.status === 409) setReplacement({ day: variables.day, kg: variables.kg })
    },
  })
  const points = [...(trend.data ?? [])].filter(point => !point.flagged).sort((a, b) => a.day.localeCompare(b.day))
  const latest = points.at(-1)
  return <>
    <h2>Weight</h2>
    <div className="weight-stats">
      <p>Latest trend: <strong>{formatDec(latest?.trend ?? null, 2)} kg</strong></p>
      <p>Trend change: <strong>{formatDec(trendChange(points[0]?.trend, latest?.trend), 2)} kg</strong></p>
    </div>
    <form className="weight-form weight-form--log" onSubmit={(e) => {
      e.preventDefault()
      if (mutation.isPending) return
      setSaved(false)
      setReplacement(null)
      mutation.mutate({ day, kg: kg.trim().replace(',', '.') })
    }}>
      <label>Today's weight (kg) <input className="weight-control" required inputMode="decimal" value={kg} disabled={mutation.isPending}
        onChange={(e) => { setKg(e.target.value); setReplacement(null); setSaved(false); mutation.reset() }} /></label>
      <Button type="submit" disabled={mutation.isPending}>Log weight</Button>
    </form>
    {replacement && <div role="group" aria-label="Replace weight">
      <p>Replace today's weight?</p>
      <Button disabled={mutation.isPending} onClick={() => mutation.mutate({ ...replacement, replace: true })}>Replace</Button>
      <Button disabled={mutation.isPending} onClick={() => { setReplacement(null); mutation.reset() }}>Cancel</Button>
    </div>}
    {saved && <p role="status">Weight saved.</p>}
    {mutation.error && !replacement && <p role="alert">{mutation.error.message}</p>}
    {trend.isPending ? <p role="status">Loading weight trend…</p> : trend.error ? <p role="alert">{trend.error.message}</p> : <TrendList points={trend.data} />}
  </>
}

export function WeightPage() {
  return <main><WeightPanel /><TargetsCard /><Link className="ui-button ui-button--ghost" to="/profile">Edit profile</Link></main>
}
