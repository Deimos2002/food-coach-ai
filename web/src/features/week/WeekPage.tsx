import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { getWeek } from '../../lib/api'
import { addWeeks } from '../../lib/dates'
import { FactCard } from './FactCard'
import { CardsPanel } from './CardsPanel'
import { CoachCard } from '../coach/CoachCard'

export function WeekPage({ isoWeek: week }: { isoWeek?: string } = {}) {
  const params = useParams<{ isoWeek: string }>()
  const isoWeek = week ?? params.isoWeek ?? ''
  const query = useQuery({ queryKey: ['week', isoWeek], queryFn: () => getWeek(isoWeek), enabled: isoWeek !== '' })
  const data = query.data

  return (
    <main>
      <nav aria-label="Week">
        <Link to={`/week/${addWeeks(isoWeek, -1)}`} aria-label="Previous week">
          {'<'}
        </Link>
        <h1>{isoWeek}</h1>
        <Link to={`/week/${addWeeks(isoWeek, 1)}`} aria-label="Next week">
          {'>'}
        </Link>
      </nav>
      {query.isPending && <p>Loading...</p>}
      {query.error && <p role="alert">{query.error.message}</p>}
      {data && (
        <>
          <p>
            {data.complete_days} of 7 days complete
            {data.fact_sheet.days_missing.length > 0 && (
              <>; missing: {data.fact_sheet.days_missing.join(', ')}</>
            )}
          </p>
          <p>{data.fact_sheet.coverage}</p>
          {data.fact_sheet.facts.map((fact) => (
            <FactCard key={fact.id} fact={fact} cards={data.cards} />
          ))}
          <CardsPanel cards={data.cards} />
          <CoachCard isoWeek={isoWeek} completeDays={data.complete_days} />
        </>
      )}
    </main>
  )
}
