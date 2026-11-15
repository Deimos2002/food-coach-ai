import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { CoachCard } from './CoachCard'
import { ready } from './fixtures'

const server = setupServer(
  http.get('*/coach/weeks/:week', () => HttpResponse.json({ detail: 'no review' }, { status: 404 })),
)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('generate_shows_suggestions', async () => {
  server.use(http.post('*/coach/weeks/2026-W42', ({ request }) => {
    expect(request.headers.get('Authorization')).toBe('Bearer test-token')
    expect(new URL(request.url).searchParams.get('regenerate')).toBe('false')
    return HttpResponse.json(ready)
  }))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByRole('button', { name: "Get this week's suggestions" }))
  expect(await screen.findByRole('heading', { name: ready.review.suggestions[0].title })).toBeInTheDocument()
})

it('why_lists_cited_facts_and_swaps_from_the_stored_review', async () => {
  server.use(http.get('*/coach/weeks/:week', () => HttpResponse.json(ready)))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByText('Why?'))
  expect(screen.getByText(ready.review.fact_sheet.facts[0]!.message)).toBeVisible()
  expect(screen.getByText('Peach Drink Sugarfree')).toBeVisible()
  expect(screen.getByText(/11.*0.*g\/100ml/)).toBeVisible()
})

it('unavailable_shows_the_reason_and_no_static_suggestions', async () => {
  const message = "AI suggestions are off: EU-only processing isn't available yet."
  server.use(http.get('*/coach/weeks/:week', () => HttpResponse.json({
    status: 'unavailable', message,
    review: { ...ready.review, source: 'template', fallback_reason: 'none', suggestions: [] },
  })))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(screen.queryByText(ready.review.suggestions[0].body)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Regenerate' })).toBeInTheDocument()
})

it('insufficient_data_message_without_a_generate_button', async () => {
  server.use(http.post('*/coach/weeks/:week', () => HttpResponse.json({ status: 'insufficient_data', complete_days: 2 })))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByRole('button', { name: "Get this week's suggestions" }))
  expect(await screen.findByText(/At least 3 complete days.*2 complete days/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /suggestions|Regenerate/ })).not.toBeInTheDocument()
})

it('known_insufficient_week_never_offers_generation', () => {
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={2} />)
  expect(screen.getByText(/At least 3 complete days.*2 complete days/)).toBeInTheDocument()
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
})

it('nothing_to_suggest_shows_message', async () => {
  server.use(http.get('*/coach/weeks/:week', () => HttpResponse.json({
    status: 'nothing_to_suggest', message: 'Nothing to suggest this week.',
    review: { ...ready.review, source: 'template', suggestions: [] },
  })))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  expect(await screen.findByText('Nothing to suggest this week.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /suggestions|Regenerate/ })).not.toBeInTheDocument()
})

it('regenerate_409_hides_button_and_preserves_suggestions', async () => {
  server.use(
    http.get('*/coach/weeks/:week', () => HttpResponse.json(ready)),
    http.post('*/coach/weeks/:week', ({ request }) => {
      expect(new URL(request.url).searchParams.get('regenerate')).toBe('true')
      return HttpResponse.json({ detail: 'Already regenerated' }, { status: 409 })
    }),
  )
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Regenerate' }))
  expect(await screen.findByText('Already regenerated this week')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Regenerate' })).not.toBeInTheDocument()
  expect(screen.getByText(ready.review.suggestions[0].body)).toBeInTheDocument()
})

it('generation_two_hides_regenerate_on_reload', async () => {
  server.use(http.get('*/coach/weeks/:week', () => HttpResponse.json({
    ...ready, review: { ...ready.review, generation_no: 2 },
  })))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await screen.findByText(ready.review.suggestions[0].body)
  expect(screen.queryByRole('button', { name: 'Regenerate' })).not.toBeInTheDocument()
})

it.each([['Helpful', 'up'], ['Not helpful', 'down'], ["I'll try this", 'will_try']])(
  'feedback_posts_value %s', async (label, value) => {
    let body: unknown
    server.use(
      http.get('*/coach/weeks/:week', () => HttpResponse.json(ready)),
      http.post('*/coach/reviews/42/feedback', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ id: 7, review_id: 42, suggestion_index: 0, value }, { status: 201 })
      }),
    )
    renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
    await userEvent.click(await screen.findByRole('button', { name: label }))
    expect(await screen.findByText('Thanks for your feedback')).toBeInTheDocument()
    expect(body).toEqual({ suggestion_index: 0, value })
  },
)

it('post_202_polls_GET_until_ready_without_posting_again', async () => {
  let posted = false
  let reads = 0
  let posts = 0
  server.use(
    http.get('*/coach/weeks/:week', () => {
      if (!posted) return HttpResponse.json({ detail: 'no review' }, { status: 404 })
      reads++
      return reads < 2 ? HttpResponse.json({ status: 'in_progress' }, { status: 202 }) : HttpResponse.json(ready)
    }),
    http.post('*/coach/weeks/:week', () => {
      posted = true
      posts++
      return HttpResponse.json({ status: 'in_progress' }, { status: 202 })
    }),
  )
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByRole('button', { name: "Get this week's suggestions" }))
  expect(await screen.findByRole('status', { name: 'Generating suggestions' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Generating…' })).toBeDisabled()
  await waitFor(() => expect(screen.getByText(ready.review.suggestions[0].body)).toBeInTheDocument(), { timeout: 5000 })
  expect(posts).toBe(1)
  expect(reads).toBe(2)
})

it('existing_in_progress_review_polls_on_mount', async () => {
  let reads = 0
  server.use(http.get('*/coach/weeks/:week', () => {
    reads++
    return reads === 1 ? HttpResponse.json({ status: 'in_progress' }, { status: 202 }) : HttpResponse.json(ready)
  }))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await screen.findByRole('status', { name: 'Generating suggestions' })
  await waitFor(() => expect(screen.getByText(ready.review.suggestions[0].body)).toBeInTheDocument(), { timeout: 3500 })
})

it('generation_button_is_disabled_during_the_POST', async () => {
  let finish = () => {}
  server.use(http.post('*/coach/weeks/:week', async () => {
    await new Promise<void>((resolve) => { finish = resolve })
    return HttpResponse.json(ready)
  }))
  renderWithProviders(<CoachCard isoWeek="2026-W42" completeDays={3} />)
  await userEvent.click(await screen.findByRole('button', { name: "Get this week's suggestions" }))
  expect(await screen.findByRole('button', { name: 'Generating…' })).toBeDisabled()
  finish()
  await screen.findByText(ready.review.suggestions[0].body)
})
