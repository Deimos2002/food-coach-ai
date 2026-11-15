import { Link } from 'react-router-dom'
import { Card } from '../../ui/Card'
import { PageHeader } from '../../ui/PageHeader'
import { TrialStats } from '../settings/TrialStats'
import { CoachOps } from '../coach/CoachOps'
import './AdvancedPage.css'

export function AdvancedPage() {
  return <main className="me-page advanced-page">
    <PageHeader title="Advanced" actions={<Link className="me-link" to="/me">Back to Me</Link>} />
    <Card><Link className="me-destination" to="/aliases"><span>Aliases</span><span aria-hidden="true">&gt;</span></Link></Card>
    <Card><TrialStats /></Card>
    <Card><CoachOps /></Card>
  </main>
}
