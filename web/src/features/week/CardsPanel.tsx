import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { verifyCard } from '../../lib/api'
import type { GuidelineCard } from '../../lib/types'

export interface CardsPanelProps {
  cards: GuidelineCard[]
}

/** Unverified guideline cards, with a two-step "Verify" that shows the full quote and URL. */
export function CardsPanel({ cards }: CardsPanelProps) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const qc = useQueryClient()
  const mutation = useMutation({
    mutationFn: (id: string) => verifyCard(id),
    onSuccess: () => {
      setConfirming(null)
      void qc.invalidateQueries({ queryKey: ['week'] })
    },
  })
  const unverified = cards.filter((c) => c.verified_by_user_at === null)
  if (unverified.length === 0) return null
  return (
    <section aria-label="Unverified cards">
      <h2>Unverified cards</h2>
      <ul>
        {unverified.map((c) => (
          <li key={c.id}>
            <p>{c.title}</p>
            {confirming === c.id ? (
              <div role="group" aria-label={`Confirm verify ${c.id}`}>
                <p>&ldquo;{c.quote}&rdquo;</p>
                <p>
                  <a href={c.url} target="_blank" rel="noreferrer">
                    {c.url}
                  </a>
                </p>
                <button
                  type="button"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate(c.id)}
                >
                  Confirm verify
                </button>
                <button type="button" disabled={mutation.isPending} onClick={() => setConfirming(null)}>
                  Cancel
                </button>
              </div>
            ) : (
              <button type="button" onClick={() => setConfirming(c.id)}>
                Verify
              </button>
            )}
          </li>
        ))}
      </ul>
      {mutation.error && <p role="alert">{mutation.error.message}</p>}
    </section>
  )
}
