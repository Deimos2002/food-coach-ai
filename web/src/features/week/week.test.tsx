import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import type { WeekResponse, Fact, GuidelineCard } from '../../lib/types'
import { WeekPage } from './WeekPage'
import { isoWeekOf, addWeeks } from '../../lib/dates'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const WEEK = '2026-W41'

function baseFact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: 'F1',
    rule: 'protein_short',
    claim: 'below',
    values: {},
    days: ['2026-10-12', '2026-10-13'],
    card_id: null,
    message: 'Protein was below target most days.',
    contributors: [],
    unknown_for: [],
    additions_undeclared: [],
    swaps: [],
    ...overrides,
  }
}

function weekResponse(overrides: Partial<WeekResponse> = {}): WeekResponse {
  return {
    fact_sheet: {
      schema_version: 1,
      week: WEEK,
      coverage: '4 complete days; Sat 17 Oct not logged.',
      days_included: ['2026-10-12', '2026-10-13'],
      days_missing: ['2026-10-17'],
      facts: [baseFact()],
      goal: null,
      trend: null,
      hash: 'abc',
    },
    complete_days: 4,
    cards: [],
    ...overrides,
  }
}

function renderWeek(isoWeek = WEEK) {
  renderWithProviders(
    <Routes>
      <Route path="/week/:isoWeek" element={<WeekPage />} />
    </Routes>,
    { route: `/week/${isoWeek}` },
  )
}

describe('WeekPage', () => {
  it('week_shows_fact_message_and_days', async () => {
    server.use(http.get('*/week/:isoWeek', () => HttpResponse.json(weekResponse())))
    renderWeek()
    expect(await screen.findByText('Protein was below target most days.')).toBeInTheDocument()
    expect(screen.getByText(/2026-10-12, 2026-10-13/)).toBeInTheDocument()
    expect(screen.getByText(/4 of 7 days complete/)).toBeInTheDocument()
    expect(screen.getByText(/missing: 2026-10-17/)).toBeInTheDocument()
  })

  it('contributor_links_to_day', async () => {
    const fact = baseFact({
      contributors: [
        {
          id: 'C1',
          name: 'Greek yoghurt',
          share: ['0.38', '0.5'],
          additions_undeclared: false,
          item_refs: [{ day: '2026-10-12', meal_id: 1, item_id: 1 }],
        },
      ],
    })
    server.use(http.get('*/week/:isoWeek', () => HttpResponse.json(weekResponse({ fact_sheet: { ...weekResponse().fact_sheet, facts: [fact] } }))))
    renderWeek()
    const link = await screen.findByRole('link', { name: 'Greek yoghurt' })
    expect(link).toHaveAttribute('href', '/day/2026-10-12')
    expect(screen.getByText(/38–50 %/)).toBeInTheDocument()
  })

  it('unknown_for_listed', async () => {
    const fact = baseFact({ unknown_for: ['salt', 'fibre'] })
    server.use(http.get('*/week/:isoWeek', () => HttpResponse.json(weekResponse({ fact_sheet: { ...weekResponse().fact_sheet, facts: [fact] } }))))
    renderWeek()
    expect(await screen.findByText(/Unknown for: salt, fibre/)).toBeInTheDocument()
  })

  it('swap_links_to_product', async () => {
    const withId = baseFact({
      id: 'F2',
      swaps: [
        {
          id: 'S1',
          for: 'C1',
          name: 'Low-salt crisps',
          product_id: 99,
          values: { from_lower: '2', to_upper: '0.5', unit: 'g/100g', basis: 'catalogue_per_100' },
        },
      ],
    })
    const withoutId = baseFact({
      id: 'F3',
      swaps: [
        {
          id: 'S2',
          for: 'C1',
          name: 'No-ID swap',
          values: { from_lower: '2', to_upper: '0.5', unit: 'g/100g', basis: 'catalogue_per_100' },
        },
      ],
    })
    server.use(
      http.get('*/week/:isoWeek', () =>
        HttpResponse.json(weekResponse({ fact_sheet: { ...weekResponse().fact_sheet, facts: [withId, withoutId] } })),
      ),
    )
    renderWeek()
    const link = await screen.findByRole('link', { name: 'Low-salt crisps' })
    expect(link).toHaveAttribute('href', '/products/99')
    expect(screen.getByText('No-ID swap')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'No-ID swap' })).not.toBeInTheDocument()
  })

  it('card_quote_shown_only_with_card_id', async () => {
    const card: GuidelineCard = {
      id: 'CARD1',
      source_org: 'WHO',
      title: 'Sugar guideline',
      quote: 'Keep free sugars below 10% of energy intake.',
      url: 'https://example.org/who-sugar',
      retrieved_on: '2026-01-01',
      threshold: '10',
      unit: '%',
      notes: null,
      verified_by_user_at: '2026-01-02T00:00:00Z',
    }
    const withCard = baseFact({ id: 'F4', card_id: 'CARD1' })
    const withoutCard = baseFact({ id: 'F5', card_id: null, message: 'No card here.' })
    server.use(
      http.get('*/week/:isoWeek', () =>
        HttpResponse.json(
          weekResponse({
            fact_sheet: { ...weekResponse().fact_sheet, facts: [withCard, withoutCard] },
            cards: [card],
          }),
        ),
      ),
    )
    renderWeek()
    expect(await screen.findByText(/Keep free sugars below 10% of energy intake\./)).toBeInTheDocument()
    const noCardArticle = screen.getByLabelText('Fact F5')
    expect(within(noCardArticle).queryByText(/Keep free sugars/)).not.toBeInTheDocument()
  })

  it('verify_card_requires_confirm', async () => {
    const card: GuidelineCard = {
      id: 'CARD2',
      source_org: 'WHO',
      title: 'Salt guideline',
      quote: 'Keep salt below 5g a day.',
      url: 'https://example.org/who-salt',
      retrieved_on: '2026-01-01',
      threshold: '5',
      unit: 'g',
      notes: null,
      verified_by_user_at: null,
    }
    let verifyCalled = false
    server.use(
      http.get('*/week/:isoWeek', () => HttpResponse.json(weekResponse({ cards: [card] }))),
      http.post('*/cards/CARD2/verify', () => {
        verifyCalled = true
        return HttpResponse.json({ ...card, verified_by_user_at: '2026-01-03T00:00:00Z' })
      }),
    )
    renderWeek()
    const user = userEvent.setup()
    await screen.findByText('Salt guideline')
    expect(screen.queryByText(/Keep salt below 5g a day\./)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(screen.getByText(/Keep salt below 5g a day\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://example.org/who-salt' })).toBeInTheDocument()
    expect(verifyCalled).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Confirm verify' }))
    await waitFor(() => expect(verifyCalled).toBe(true))
  })
})

describe('isoWeekOf / addWeeks', () => {
  it('iso_week_of_handles_year_boundary', () => {
    // 2026-12-31 is a Thursday, ISO week 53 of 2026.
    expect(isoWeekOf('2026-12-31')).toBe('2026-W53')
    // 2027-01-01 is a Friday, still ISO week 53 of 2026.
    expect(isoWeekOf('2027-01-01')).toBe('2026-W53')
    // 2027-01-04 is a Monday, ISO week 1 of 2027.
    expect(isoWeekOf('2027-01-04')).toBe('2027-W01')
    expect(addWeeks('2026-W53', 1)).toBe('2027-W01')
    expect(addWeeks('2027-W01', -1)).toBe('2026-W53')
  })
})
