import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { handlers, emptyDay } from '../../test/handlers'
import { renderWithProviders } from '../../test/render'
import { localDay } from '../../lib/dates'
import * as trial from '../../lib/trial'
import { SettingsPage } from './SettingsPage'

const server = setupServer(...handlers)
beforeAll(() => server.listen({ onUnhandledRequest: 'error' } as Parameters<typeof server.listen>[0]))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// Keep a stable "now" so localDay()-based day fetches in the component match what the
// test expects, and so the test is not sensitive to the real clock.
beforeEach(() => {
  vi.useRealTimers()
})

describe('settings page', () => {
  it('export_downloads_json', async () => {
    server.use(
      http.get('*/export', () =>
        HttpResponse.json({ generation: 3, exported_at: '2026-10-13T08:00:00Z', data: { products: [] } }),
      ),
    )
    const createObjectURL = vi.fn(() => 'blob:fake-url')
    const revokeObjectURL = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    renderWithProviders(<SettingsPage />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Export my data' }))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(clickSpy).toHaveBeenCalled()
    const expectedName = `foodcoach-export-${localDay()}.json`
    const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement
    expect(anchor.download).toBe(expectedName)

    clickSpy.mockRestore()
  })

  it('delete_requires_typed_confirmation', async () => {
    let deleteCalled = false
    server.use(
      http.delete('*/me/data', () => {
        deleteCalled = true
        return HttpResponse.json({ generation: 4, deleted: { meal_item: 2 } })
      }),
    )
    renderWithProviders(<SettingsPage />)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Delete all my data' }))

    const dialog = await screen.findByRole('dialog', { name: 'Delete all my data' })
    const confirmBtn = within(dialog).getByRole('button', { name: 'Delete everything' })
    expect(confirmBtn).toBeDisabled()

    await user.type(within(dialog).getByLabelText(/type DELETE to confirm/i), 'nope')
    expect(confirmBtn).toBeDisabled()
    expect(deleteCalled).toBe(false)

    await user.clear(within(dialog).getByLabelText(/type DELETE to confirm/i))
    await user.type(within(dialog).getByLabelText(/type DELETE to confirm/i), 'DELETE')
    expect(confirmBtn).not.toBeDisabled()
    await user.click(confirmBtn)

    await waitFor(() => expect(deleteCalled).toBe(true))
    expect(await screen.findByText('All your data was deleted')).toBeInTheDocument()
  })

  it('trial_stats_show_medians_and_target', async () => {
    vi.spyOn(trial, 'stats').mockReturnValue({
      cold: { n: 3, medianMs: 52000 },
      warm: { n: 10, medianMs: 21000 },
    })
    renderWithProviders(<SettingsPage />)
    await screen.findByText(/45 s/)
    expect(screen.getByText(/52(\.0)? s/)).toBeInTheDocument()
    expect(screen.getByText(/n=3/)).toBeInTheDocument()
    expect(screen.getByText(/21(\.0)? s/)).toBeInTheDocument()
    expect(screen.getByText(/n=10/)).toBeInTheDocument()
  })

  it('complete_day_rate_over_14_days', async () => {
    vi.spyOn(trial, 'stats').mockReturnValue({ cold: { n: 0, medianMs: null }, warm: { n: 0, medianMs: null } })
    const requestedDays: string[] = []
    server.use(
      http.get('*/days/:day', ({ params }) => {
        const day = String(params.day)
        requestedDays.push(day)
        const view = emptyDay(day)
        // Mark every other requested day complete, deterministically by day string.
        const complete = parseInt(day.slice(-2), 10) % 2 === 0
        return HttpResponse.json({ ...view, status: complete ? 'complete' : 'partial' })
      }),
    )
    renderWithProviders(<SettingsPage />)

    // The 14-day fetch must not happen until the stats section is opened.
    expect(requestedDays).toHaveLength(0)
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Usage stats' }))

    await waitFor(() => expect(requestedDays.length).toBe(14))
    expect(await screen.findByText(/complete-day rate/i)).toBeInTheDocument()
  })
})
