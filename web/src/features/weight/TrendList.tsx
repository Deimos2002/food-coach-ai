import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { confirmWeight, deleteWeight, putWeight } from '../../lib/api'
import { formatDec, sparklinePoints } from '../../lib/format'
import type { WeightTrendPoint } from '../../lib/types'

import { Button } from '../../ui/Button'
import '../progress/ProgressPage.css'

function TrendEntry({ point }: { point: WeightTrendPoint }) {
  const cache = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [kg, setKg] = useState('')
  const [deleting, setDeleting] = useState(false)
  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ['weight-trend'] }),
      cache.invalidateQueries({ queryKey: ['targets'] }),
    ])
  }
  const edit = useMutation({
    mutationFn: () => putWeight(point.day, kg.trim().replace(',', '.'), true),
    onSuccess: async () => { setEditing(false); setKg(''); await refresh() },
  })
  const remove = useMutation({ mutationFn: () => deleteWeight(point.day), onSuccess: refresh })
  const confirm = useMutation({ mutationFn: () => confirmWeight(point.day), onSuccess: refresh })
  const pending = edit.isPending || remove.isPending || confirm.isPending
  return <li className="weight-entry">
    <p>{point.day} · Trend: {formatDec(point.trend, 2)} kg</p>
    {point.flagged && <><p>Outlier — excluded</p>
      <Button disabled={pending} onClick={() => confirm.mutate()}>Confirm real</Button></>}
    {point.suggest_confirm && <p>Real change?</p>}
    <Button disabled={pending} aria-label={`Edit weight ${point.day}`} onClick={() => { setEditing(true); edit.reset() }}>Edit</Button>
    <Button disabled={pending} aria-label={`Delete weight ${point.day}`} onClick={() => setDeleting(true)}>Delete</Button>
    {editing && <form className="weight-form" onSubmit={(e) => { e.preventDefault(); if (!pending) edit.mutate() }}>
      <label>Replacement weight for {point.day} (kg)
        <input className="weight-control" required inputMode="decimal" disabled={pending} value={kg} onChange={(e) => setKg(e.target.value)} />
      </label>
      <Button type="submit" disabled={pending}>Save replacement</Button>
      <Button type="button" disabled={pending} onClick={() => setEditing(false)}>Cancel edit</Button>
    </form>}
    {deleting && <div role="group" aria-label={`Confirm deletion ${point.day}`}>
      <p>Delete the weight for {point.day}?</p>
      <Button disabled={pending} onClick={() => remove.mutate()}>Confirm delete</Button>
      <Button disabled={pending} onClick={() => setDeleting(false)}>Cancel delete</Button>
    </div>}
    {[edit.error, remove.error, confirm.error].filter(Boolean).map((error, index) => <p role="alert" key={index}>{error?.message}</p>)}
  </li>
}

export function TrendList({ points }: { points: WeightTrendPoint[] }) {
  const recent = [...points].sort((a, b) => a.day.localeCompare(b.day)).slice(-30)
  if (!recent.length) return <p>No weights logged yet.</p>
  return <section>
    <svg role="img" aria-label="Weight trend over the last 30 entries" viewBox="0 0 300 60" className="weight-trend">
      <polyline points={sparklinePoints(recent.map((point) => point.trend))} fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
    <details className="weight-history" open={recent.some(point => point.flagged || point.suggest_confirm)}>
      <summary>Weight history ({recent.length} {recent.length === 1 ? 'entry' : 'entries'})</summary>
      <ul className="weight-list">{recent.toReversed().map((point) => <TrendEntry key={point.day} point={point} />)}</ul>
    </details>
  </section>
}
