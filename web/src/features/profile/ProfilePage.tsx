import { Button, PageHeader } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getProfile, putProfile } from '../../lib/api'
import type { Profile } from '../../lib/types'
import { TargetsCard } from './TargetsCard'

const defaults: Profile = { sex: 'male', birth_year: 1996, height_cm: '', activity: '1.2', goal: 'maintain', pace_pct_per_week: '0.5' }

function ProfileForm({ initial }: { initial: Profile }) {
  const cache = useQueryClient()
  const [form, setForm] = useState({ ...initial, birth_year: String(initial.birth_year) })
  const [saved, setSaved] = useState(false)
  const mutation = useMutation({
    mutationFn: putProfile,
    onSuccess: async (data) => {
      cache.setQueryData(['profile'], data)
      setSaved(true)
      await cache.invalidateQueries({ queryKey: ['targets'] })
    },
  })
  const errors: Partial<Record<keyof Profile, string>> = {}
  if (mutation.error instanceof ApiError && mutation.error.status === 422) {
    const body = mutation.error.body as { detail?: { loc?: unknown[]; msg?: string }[] } | null
    if (Array.isArray(body?.detail)) {
      for (const item of body.detail) {
        const field = item.loc?.at(-1)
        if (typeof field === 'string' && field in form && typeof item.msg === 'string') errors[field as keyof Profile] = item.msg
      }
    }
  }
  function update(field: keyof Profile, value: string) {
    setForm({ ...form, [field]: value })
    setSaved(false)
    mutation.reset()
  }
  function validation(field: keyof Profile) {
    return { 'aria-invalid': !!errors[field], 'aria-describedby': errors[field] ? `${field}-error` : undefined }
  }
  function error(field: keyof Profile) {
    return errors[field] && <p id={`${field}-error`} role="alert">{errors[field]}</p>
  }
  return <form className="restyle-form" onSubmit={(e) => {
    e.preventDefault()
    if (mutation.isPending) return
    mutation.mutate({ ...form, birth_year: parseInt(form.birth_year, 10),
      height_cm: form.height_cm.trim().replace(',', '.'), pace_pct_per_week: form.pace_pct_per_week.trim().replace(',', '.') })
  }}>
    <fieldset disabled={mutation.isPending} className="restyle-form">
      <legend>Profile inputs</legend>
      <label>Sex <select className="restyle-control" value={form.sex} onChange={(e) => update('sex', e.target.value)} {...validation('sex')}>
        <option value="male">Male</option><option value="female">Female</option>
      </select></label>{error('sex')}
      <label>Birth year <input className="restyle-control" required type="number" step="1" value={form.birth_year} onChange={(e) => update('birth_year', e.target.value)} {...validation('birth_year')} /></label>{error('birth_year')}
      <label>Height (cm) <input className="restyle-control" required inputMode="decimal" value={form.height_cm} onChange={(e) => update('height_cm', e.target.value)} {...validation('height_cm')} /></label>{error('height_cm')}
      <label>Activity <select className="restyle-control" value={form.activity} onChange={(e) => update('activity', e.target.value)} {...validation('activity')}>
        <option value="1.2">Sedentary (1.2)</option><option value="1.375">Lightly active (1.375)</option>
        <option value="1.55">Moderately active (1.55)</option><option value="1.725">Very active (1.725)</option>
      </select></label>{error('activity')}
      <label>Goal <select className="restyle-control" value={form.goal} onChange={(e) => update('goal', e.target.value)} {...validation('goal')}>
        <option value="lose">Lose</option><option value="maintain">Maintain</option><option value="gain">Gain</option>
      </select></label>{error('goal')}
      <label>Pace (%/week, at most 1) <input className="restyle-control" required inputMode="decimal" value={form.pace_pct_per_week} onChange={(e) => update('pace_pct_per_week', e.target.value)} {...validation('pace_pct_per_week')} /></label>{error('pace_pct_per_week')}
      <Button type="submit">Save profile</Button>
    </fieldset>
    {mutation.error && !Object.keys(errors).length && <p role="alert">{mutation.error.message}</p>}
    {saved && <p role="status">Profile saved.</p>}
  </form>
}

export function ProfilePage() {
  const query = useQuery({ queryKey: ['profile'], queryFn: getProfile })
  const missing = query.error instanceof ApiError && query.error.status === 404
  return <main className="restyle-page ui-card">
    <PageHeader title="Profile" />
    {query.isPending ? <p role="status">Loading profile…</p> : query.error && !missing ?
      <p role="alert">{query.error.message}</p> : <ProfileForm initial={query.data ?? defaults} />}
    <TargetsCard />
  </main>
}
