import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { handlers } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { MePage } from './MePage'
import { AdvancedPage } from './AdvancedPage'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

it('me shows profile, saved meals, your data, advanced link', async () => {
  server.use(
    http.get('*/profile', () => HttpResponse.json({ sex: 'male', birth_year: 1996, height_cm: '178', activity: '1.2', goal: 'lose', pace_pct_per_week: '0.5' })),
    http.get('*/targets', () => HttpResponse.json({ maintenance_kcal: 2400, kcal: 2100, protein_g: 135 })),
  )
  renderWithProviders(<MePage />)
  expect(await screen.findByText('Lose')).toBeInTheDocument()
  expect(await screen.findByText('2100 kcal')).toBeInTheDocument()
  expect(screen.getByText('135 g')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Edit profile' })).toHaveAttribute('href', '/profile')
  expect(screen.getByRole('link', { name: /Saved meals/ })).toHaveAttribute('href', '/saved')
  expect(screen.getByRole('region', { name: 'Your data' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Advanced/ })).toHaveAttribute('href', '/me/advanced')
  expect(screen.queryByRole('region', { name: 'Coach ops' })).not.toBeInTheDocument()
})

it('missing profile and targets stay unknown with a setup link', async () => {
  renderWithProviders(<MePage />)
  expect(await screen.findByRole('link', { name: 'Set your targets' })).toHaveAttribute('href', '/profile')
  const profile = screen.getByRole('region', { name: 'Profile' })
  expect(within(profile).getByText('? kcal')).toBeInTheDocument()
  expect(within(profile).getByText('? g')).toBeInTheDocument()
})

it('delete all keeps its confirmation and danger style', async () => {
  renderWithProviders(<MePage />)
  const user = userEvent.setup()
  const trigger = screen.getByRole('button', { name: 'Delete all my data' })
  expect(trigger).toHaveClass('ui-button--danger')
  await user.click(trigger)
  const dialog = screen.getByRole('dialog', { name: 'Delete all my data' })
  const confirm = within(dialog).getByRole('button', { name: 'Delete everything' })
  expect(confirm).toHaveClass('ui-button--danger')
  expect(confirm).toBeDisabled()
  await user.type(within(dialog).getByLabelText(/Type DELETE/), 'delete')
  expect(confirm).toBeDisabled()
  await user.clear(within(dialog).getByLabelText(/Type DELETE/))
  await user.type(within(dialog).getByLabelText(/Type DELETE/), 'DELETE')
  expect(confirm).toBeEnabled()
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('advanced holds aliases, usage stats, coach ops', async () => {
  renderWithProviders(<AdvancedPage />)
  expect(screen.getByRole('heading', { name: 'Advanced' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Aliases/ })).toHaveAttribute('href', '/aliases')
  expect(screen.getByRole('region', { name: 'Usage stats' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Coach ops' })).toBeInTheDocument()
  expect(await screen.findByText('Calls')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Delete all my data' })).not.toBeInTheDocument()
})
