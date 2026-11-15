import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { addItem, apiBase, ApiError, apiFetch, getDay } from './api'
import { setTokenProvider } from './auth'
import type { AddItemPayload } from './types'

const server = setupServer()
const payload: AddItemPayload = { product_id: 1, amount: '150', unit: 'g', quantity_source: 'weighed' }

beforeAll(() => server.listen())
beforeEach(() => setTokenProvider({ getToken: async () => 'tok' }))
afterEach(() => {
  server.resetHandlers()
  vi.useRealTimers()
})
afterAll(() => server.close())

describe('apiFetch', () => {
  it('sends the bearer and Idempotency-Key headers', async () => {
    let auth: string | null = null
    let key: string | null = null
    server.use(
      http.post('*/meals/7/items', ({ request }) => {
        auth = request.headers.get('Authorization')
        key = request.headers.get('Idempotency-Key')
        return HttpResponse.json({ item_id: 1, nutrients: {} }, { status: 201 })
      }),
    )
    await addItem(7, payload, 'key-1')
    expect(auth).toBe('Bearer tok')
    expect(key).toBe('key-1')
  })

  it('turns a 422 detail into an ApiError', async () => {
    server.use(http.get('*/days/2030-01-01', () => HttpResponse.json({ detail: 'bad day' }, { status: 422 })))
    const err = await getDay('2030-01-01').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).detail).toBe('bad day')
  })

  it('turns a 401 into "Signed out"', async () => {
    server.use(http.get('*/profile', () => HttpResponse.json({ detail: 'unauthorized' }, { status: 401 })))
    const err = await apiFetch('/profile').catch((e: unknown) => e)
    expect((err as ApiError).status).toBe(401)
    expect((err as ApiError).detail).toBe('Signed out')
  })
})

describe('apiBase', () => {
  it('falls back to the stage-1 default backend, not window.location.origin', () => {
    expect(import.meta.env.VITE_API_URL).toBeUndefined()
    expect(apiBase()).toBe('http://localhost:8000')
    expect(apiBase()).not.toBe(window.location.origin)
  })
})

describe('addItem retry', () => {
  it('retries once after a network failure with the same key', async () => {
    const keys: (string | null)[] = []
    server.use(
      http.post('*/meals/7/items', ({ request }) => {
        keys.push(request.headers.get('Idempotency-Key'))
        if (keys.length === 1) return HttpResponse.error()
        return HttpResponse.json({ item_id: 5, nutrients: {} }, { status: 201 })
      }),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const p = addItem(7, payload, 'same-key')
    await vi.advanceTimersByTimeAsync(600)
    const res = await p
    expect(res.item_id).toBe(5)
    expect(keys).toEqual(['same-key', 'same-key'])
  })

  it('does not retry on a 500', async () => {
    let calls = 0
    server.use(
      http.post('*/meals/7/items', () => {
        calls += 1
        return HttpResponse.json({ detail: 'boom' }, { status: 500 })
      }),
    )
    const err = await addItem(7, payload, 'k').catch((e: unknown) => e)
    expect((err as ApiError).status).toBe(500)
    expect(calls).toBe(1)
  })
})
