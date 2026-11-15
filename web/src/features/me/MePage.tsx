import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ApiError, getProfile, getTargets } from '../../lib/api'
import { localDay } from '../../lib/dates'
import { Card } from '../../ui/Card'
import { PageHeader } from '../../ui/PageHeader'
import { YourData } from '../settings/SettingsPage'
import './MePage.css'

export function MePage() {
  const day = localDay()
  const profile = useQuery({ queryKey: ['profile'], queryFn: getProfile, retry: false })
  const targets = useQuery({ queryKey: ['targets', day], queryFn: () => getTargets(day), retry: false })
  const missing = profile.error instanceof ApiError && profile.error.status === 404
  const missingTargets = targets.error instanceof ApiError && targets.error.status === 404
  const goals = { lose: 'Lose', maintain: 'Maintain', gain: 'Gain' }
  return <main className="me-page">
    <PageHeader title="Me" />
    <Card>
      <section aria-label="Profile" className="me-profile">
        <div className="me-section-heading"><h2>Profile</h2><Link className="me-link" to="/profile">Edit profile</Link></div>
        {(profile.isPending || targets.isPending) && <p role="status">Loading profile and targets…</p>}
        {profile.error && !missing && <p role="alert">{profile.error.message}</p>}
        {targets.error && !missingTargets && <p role="alert">{targets.error.message}</p>}
        <dl className="me-targets">
          <div><dt>Goal</dt><dd>{profile.data ? goals[profile.data.goal] : '?'}</dd></div>
          <div><dt>Daily energy</dt><dd>{targets.data?.kcal ?? '?'} kcal</dd></div>
          <div><dt>Daily protein</dt><dd>{targets.data?.protein_g ?? '?'} g</dd></div>
        </dl>
        {(missing || missingTargets) && <Link className="me-link" to="/profile">Set your targets</Link>}
      </section>
    </Card>
    <Card><Link className="me-destination" to="/saved"><span>Saved meals</span><span aria-hidden="true">&gt;</span></Link></Card>
    <Card><YourData /></Card>
    <Link className="me-destination me-advanced-link" to="/me/advanced"><span>Advanced</span><span aria-hidden="true">&gt;</span></Link>
  </main>
}
