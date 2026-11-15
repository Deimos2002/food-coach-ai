import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, generateCoachReview, getCoachReview, sendCoachFeedback } from '../../lib/api'
import { formatDec } from '../../lib/format'
import type { CoachFeedbackValue, CoachResponse, CoachReview, CoachSuggestion } from '../../lib/types'

import { Button } from '../../ui/Button'
import { Card } from '../../ui/Card'
import './CoachPage.css'

function Suggestion({ suggestion, review, index }: {
  suggestion: CoachSuggestion; review: CoachReview; index: number
}) {
  const feedback = useMutation({
    mutationFn: (value: CoachFeedbackValue) => sendCoachFeedback(review.id, index, value),
  })
  const facts = review.fact_sheet.facts.filter((fact) => suggestion.fact_ids.includes(fact.id))
  const swaps = review.fact_sheet.facts.flatMap((fact) => fact.swaps)
    .filter((swap) => suggestion.swap_ids.includes(swap.id))
  return (
    <Card as="article" className="coach-suggestion">
      <h3>{suggestion.title}</h3>
      <p>{suggestion.body}</p>
      <details>
        <summary>Why?</summary>
        <ul aria-label="Cited facts">
          {facts.map((fact) => <li key={fact.id}>
            <p>{fact.message}</p>
            <p>Days: {fact.days.join(', ') || '?'}</p>
            {fact.unknown_for.length > 0 && <p>Unknown for: {fact.unknown_for.join(', ')}</p>}
            {fact.additions_undeclared.length > 0 &&
              <p>Cooking additions not declared: {fact.additions_undeclared.join(', ')}</p>}
          </li>)}
        </ul>
        {swaps.length > 0 && <ul aria-label="Cited swaps">
          {swaps.map((swap) => <li key={swap.id}>
            <p>{swap.name}</p>
            <p>Catalogue values: {formatDec(swap.values.from_lower)} → {formatDec(swap.values.to_upper)} {swap.values.unit}</p>
          </li>)}
        </ul>}
        {suggestion.sources && suggestion.sources.length > 0 && <ul aria-label="Sources">
          {suggestion.sources.map((source) => <li key={`${source.url}:${source.locator}`}>
            <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> ({source.locator})
          </li>)}
        </ul>}
      </details>
      <div role="group" aria-label={`Feedback: ${suggestion.title}`} className="coach-feedback">
        <Button variant="secondary" disabled={feedback.isPending} onClick={() => feedback.mutate('up')}>Helpful</Button>
        <Button variant="secondary" disabled={feedback.isPending} onClick={() => feedback.mutate('down')}>Not helpful</Button>
        <Button variant="secondary" disabled={feedback.isPending} onClick={() => feedback.mutate('will_try')}>I'll try this</Button>
      </div>
      {feedback.isSuccess && <p role="status">Thanks for your feedback</p>}
      {feedback.error && <p role="alert">{feedback.error.message}</p>}
    </Card>
  )
}

export interface CoachCardProps {
  isoWeek: string
  completeDays: number
}

function WeekCoach({ isoWeek, completeDays }: CoachCardProps) {
  const cache = useQueryClient()
  const [regenerateBlocked, setRegenerateBlocked] = useState(false)
  const key = ['coach', isoWeek]
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      try { return await getCoachReview(isoWeek) }
      catch (error) {
        if (error instanceof ApiError && error.status === 404) return null
        throw error
      }
    },
    retry: false,
    enabled: completeDays >= 3,
    refetchInterval: (q) => q.state.data?.status === 'in_progress' ? 1000 : false,
    refetchIntervalInBackground: true,
  })
  const generate = useMutation({
    mutationFn: (regenerate: boolean) => generateCoachReview(isoWeek, regenerate),
    onSuccess: (response) => cache.setQueryData<CoachResponse>(key, response),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) setRegenerateBlocked(true)
    },
  })
  const response = query.data
  const insufficient = completeDays < 3 || response?.status === 'insufficient_data'
  const count = response?.status === 'insufficient_data' ? response.complete_days : completeDays
  const review = response && 'review' in response ? response.review : null
  const busy = generate.isPending || response?.status === 'in_progress'
  const limited = regenerateBlocked || (review !== null && review.generation_no >= 2)
  const error = query.error ?? (regenerateBlocked ? null : generate.error)
  return (
    <section aria-label="Weekly coach" className="coach-review">
      <h2>Weekly coach</h2>
      <p>Week {isoWeek}</p>
      {insufficient ? <p>At least 3 complete days are needed for suggestions; this week has {count} complete days.</p> : <>
        {query.isPending && <p role="status">Loading coach review…</p>}
        {error && <p role="alert">{error.message}</p>}
        {query.isError && <Button onClick={() => void query.refetch()}>Retry loading review</Button>}
        {(response?.status === 'nothing_to_suggest' || response?.status === 'unavailable') &&
          <p role="note">{response.message}</p>}
        {busy && <>
          <p role="status" aria-label="Generating suggestions">
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 20 20">
              <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="35 15">
                <animateTransform attributeName="transform" type="rotate" from="0 10 10" to="360 10 10" dur="1s" repeatCount="indefinite" />
              </circle>
            </svg>{' '}Generating suggestions…
          </p>
          <Button disabled>Generating…</Button>
        </>}
        {!busy && response === null &&
          <Button onClick={() => generate.mutate(false)}>Get this week's suggestions</Button>}
        {review?.suggestions.map((suggestion, index) => (
          <Suggestion key={`${review.id}:${index}`} suggestion={suggestion} review={review} index={index} />
        ))}
        {!busy && review && (response?.status === 'ok' || response?.status === 'unavailable') && !limited &&
          <Button onClick={() => generate.mutate(true)}>Regenerate</Button>}
        {limited && <p>Already regenerated this week</p>}
      </>}
    </section>
  )
}

/** Week-scoped mutation state must not leak when the selected week changes. */
export function CoachCard(props: CoachCardProps) {
  return <WeekCoach key={props.isoWeek} {...props} />
}
