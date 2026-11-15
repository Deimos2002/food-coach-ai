import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { ProfilePage } from './ProfilePage'
import { TargetsCard } from './TargetsCard'

beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => { cleanup(); server.resetHandlers() })
afterAll(() => server.close())

const profile = { sex: 'male', birth_year: 1996, height_cm: '180', activity: '1.55', goal: 'lose', pace_pct_per_week: '0.5' }

it('profile_422_shown_on_field', async () => {
  server.use(
    http.get('*/profile', () => HttpResponse.json(profile)),
    http.put('*/profile', () => HttpResponse.json({ detail: [{ loc: ['body', 'height_cm'], msg: 'Height is invalid', type: 'value_error' }] }, { status: 422 })),
    http.get('*/targets', () => HttpResponse.json({ detail: 'no weight' }, { status: 404 })),
  )
  renderWithProviders(<ProfilePage />)
  const user = userEvent.setup()
  await screen.findByDisplayValue('180')
  await user.click(screen.getByRole('button', { name: 'Save profile' }))
  await waitFor(() => expect(screen.getByLabelText('Height (cm)')).toHaveAccessibleDescription('Height is invalid'))
})

it('targets_missing_inputs_message', async () => {
  server.use(http.get('*/targets', () => HttpResponse.json({ detail: 'no profile set' }, { status: 404 })))
  renderWithProviders(<TargetsCard />)
  expect(await screen.findByText('Set your profile and log a weight to get targets')).toBeInTheDocument()
})

it('targets_render_integer_responses_through_display_helpers', async () => {
  server.use(http.get('*/targets', () => HttpResponse.json({ maintenance_kcal: 2759, kcal: 2319, protein_g: 144 })))
  renderWithProviders(<TargetsCard />)
  expect(await screen.findByText(/2\s*759 kcal/)).toBeInTheDocument()
  expect(screen.getByText(/2\s*319 kcal/)).toBeInTheDocument()
  expect(screen.getByText('144 g')).toBeInTheDocument()
})

it('profile_sends_decimal_strings_and_refreshes_targets', async () => {
  let body: unknown
  server.use(
    http.get('*/profile', () => HttpResponse.json(profile)),
    http.get('*/targets', () => HttpResponse.json({ detail: 'no weight' }, { status: 404 })),
    http.put('*/profile', async ({ request }) => { body = await request.json(); return HttpResponse.json(profile) }),
  )
  const { queryClient } = renderWithProviders(<ProfilePage />)
  queryClient.setQueryData(['targets', '2000-01-01'], {})
  const user = userEvent.setup()
  const height = await screen.findByLabelText('Height (cm)')
  await user.clear(height)
  await user.type(height, '180,5')
  await user.selectOptions(screen.getByLabelText('Activity'), '1.725')
  await user.click(screen.getByRole('button', { name: 'Save profile' }))
  await waitFor(() => expect(body).toEqual({ ...profile, height_cm: '180.5', activity: '1.725' }))
  await waitFor(() => expect(queryClient.getQueryState(['targets', '2000-01-01'])?.isInvalidated).toBe(true))
})
