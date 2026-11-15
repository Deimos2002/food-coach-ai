import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import App from './App'
import { setTokenProvider } from './lib/auth'
import { emptyDay, handlers } from './test/handlers'

const server = setupServer(...handlers,
  http.get('*/week/:week', ({ params }) => HttpResponse.json({
    iso_week: params.week, complete_days: 0,
    fact_sheet: { days_missing: [], coverage: 'No complete days', facts: [] }, cards: [],
  })),
)
beforeAll(() => {
  setTokenProvider({ getToken: async () => 'test-token' })
  server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0])
})
afterEach(() => {
  server.resetHandlers()
  window.history.replaceState(null, '', '/')
})
afterAll(() => server.close())

describe('App shell', () => {
  it('shell shows four destinations', async () => {
    render(<App />)

    expect(screen.getByText('Foodcoach')).toBeInTheDocument()
    expect(screen.getByText('Eat well, feel good')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Today' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Progress' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Coach' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Me' })).toBeInTheDocument()

    expect(within(screen.getByRole('navigation', { name: 'Primary' })).getAllByRole('link')).toHaveLength(4)

    const user = userEvent.setup()
    await user.click(screen.getByRole('link', { name: 'Me' }))

    expect(
      await screen.findByRole('heading', { name: 'Me' }),
    ).toBeInTheDocument()
  })
})

it.each(['/week', '/week/2026-W42', '/weight', '/settings'])('redirects old routes: %s', async (path) => {
  window.history.replaceState(null, '', path)
  render(<App />)
  const heading = path === '/settings' ? 'Me' : 'Progress'
  expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
  expect(window.location.pathname).toBe(path === '/settings' ? '/me' : '/progress')
})

it.each([
  ['/', 'Today'], ['/day/2026-10-12', 'Today'],
  ['/progress', 'Progress'], ['/coach', 'Coach'], ['/me', 'Me'], ['/me/advanced', 'Me'],
  ['/add/2026-10-12/42', 'Today'],
])('active destination has aria-current at %s', (path, name) => {
  window.history.replaceState(null, '', path)
  render(<App />)
  const nav = within(screen.getByRole('navigation', { name: 'Primary' }))
  expect(nav.getByRole('link', { name })).toHaveAttribute('aria-current', 'page')
  expect(nav.getAllByRole('link').filter(link => link.hasAttribute('aria-current'))).toHaveLength(1)
})

it('coach route shows the current weekly coach section', async () => {
  window.history.replaceState(null, '', '/coach')
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Weekly coach' })).toBeInTheDocument()
})

it('advanced route keeps advanced settings available', async () => {
  window.history.replaceState(null, '', '/me/advanced')
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Advanced' })).toBeInTheDocument()
})

it('add route closes back to the same day', async () => {
  window.history.replaceState(null, '', '/add/2026-10-12/42')
  render(<App />)
  expect(screen.getByRole('main', { name: 'Add food' })).toBeInTheDocument()
  await userEvent.setup().click(screen.getByRole('button', { name: 'Done' }))
  expect(await screen.findByRole('button', { name: 'Choose date 2026-10-12' })).toBeInTheDocument()
  expect(window.location.pathname).toBe('/day/2026-10-12')
})

it.each(['breakfast', '0', '9007199254740992'])('invalid add meal %s returns to the day', async (meal) => {
  window.history.replaceState(null, '', `/add/2026-10-12/${meal}`)
  render(<App />)
  expect(await screen.findByRole('button', { name: 'Choose date 2026-10-12' })).toBeInTheDocument()
  expect(screen.queryByRole('main', { name: 'Add food' })).not.toBeInTheDocument()
})

it.each(['Done', 'browser back'])('adding then leaving with %s returns to the same day and persisted item', async (leave) => {
  const day = leave === 'Done' ? '2026-10-22' : '2026-10-23'
  let persisted = false
  const item = { item_id: 901, name: 'Pear', requested_product_id: 91, resolved_product_id: 91, profile_revision_id: 1, quantity: { kind: 'recorded', value: '100', lower: '100', upper: '100', unit: 'g', source: 'user_estimate' }, nutrients: {}, logged_at: `${day}T12:00:00Z`, consumed_at: null }
  server.use(http.get('*/search', () => HttpResponse.json([{ product_id: 91, name: 'Pear', brand: null, basis_unit: 'g', portions: [], kcal_per_100: null }])),
    http.post('*/meals/42/items', () => { persisted = true; return HttpResponse.json({ item_id: 901, nutrients: {} }) }),
    http.get(`*/days/${day}`, () => HttpResponse.json({ ...emptyDay(day), meals: [{ meal_id: 42, meal_type: 'lunch', skipped: false, items: persisted ? [item] : [] }] })))
  window.history.replaceState(null, '', `/day/${day}`)
  window.history.pushState(null, '', `/add/${day}/42`)
  render(<App />)
  const user = userEvent.setup()
  await user.type(screen.getByRole('searchbox'), 'pear')
  await user.click(await screen.findByRole('button', { name: 'Add Pear' }))
  await screen.findByRole('button', { name: 'Undo Pear' })
  if (leave === 'Done') await user.click(screen.getByRole('button', { name: 'Done' }))
  else window.history.back()
  expect(await screen.findByRole('button', { name: `Choose date ${day}` })).toBeInTheDocument()
  expect(window.location.pathname).toBe(`/day/${day}`)
  expect(await screen.findByText('Pear')).toBeInTheDocument()
  expect(persisted).toBe(true)
})
