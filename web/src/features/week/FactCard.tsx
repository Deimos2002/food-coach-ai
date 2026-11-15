import { Link } from 'react-router-dom'
import type { Fact, GuidelineCard } from '../../lib/types'
import { formatRange, formatShareRange } from '../../lib/format'

export interface FactCardProps {
  fact: Fact
  /** All cards for this week, so the card named by `fact.card_id` can be shown. */
  cards: GuidelineCard[]
}

export function FactCard({ fact, cards }: FactCardProps) {
  const card = fact.card_id ? cards.find((c) => c.id === fact.card_id) ?? null : null
  return (
    <article aria-label={`Fact ${fact.id}`}>
      <p>{fact.message}</p>
      {fact.days.length > 0 && <p>Days: {fact.days.join(', ')}</p>}
      {fact.contributors.length > 0 && (
        <ul aria-label="Contributors">
          {fact.contributors.map((c) => {
            const day = c.item_refs[0]?.day
            return (
              <li key={c.id}>
                {day ? <Link to={`/day/${day}`}>{c.name}</Link> : <span>{c.name}</span>}
                {' — '}
                {formatShareRange(c.share)}
                {c.additions_undeclared && <span> (cooking additions not declared)</span>}
              </li>
            )
          })}
        </ul>
      )}
      {fact.unknown_for.length > 0 && (
        <p>Unknown for: {fact.unknown_for.join(', ')}</p>
      )}
      {fact.additions_undeclared.length > 0 && (
        <p>Cooking additions not declared: {fact.additions_undeclared.join(', ')}</p>
      )}
      {fact.swaps.length > 0 && (
        <ul aria-label="Swaps">
          {fact.swaps.map((s) => (
            <li key={s.id}>
              {s.product_id !== undefined ? (
                <Link to={`/products/${s.product_id}`}>{s.name}</Link>
              ) : (
                <span>{s.name}</span>
              )}
              {' — '}
              {formatRange(s.values.from_lower, s.values.to_upper, s.values.unit)}
            </li>
          ))}
        </ul>
      )}
      {card && (
        <p>
          <cite>{card.source_org}</cite>: &ldquo;{card.quote}&rdquo; —{' '}
          <a href={card.url} target="_blank" rel="noreferrer">
            {card.title}
          </a>{' '}
          ({card.retrieved_on})
        </p>
      )}
    </article>
  )
}
