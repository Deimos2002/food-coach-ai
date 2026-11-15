import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { returnTarget } from './returnTarget'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 9, 13, 12, 0, 0))
})
afterEach(() => vi.useRealTimers())

it('returns null without meal and day params (keeps old behaviour)', () => {
  expect(returnTarget(new URLSearchParams(''))).toBeNull()
  expect(returnTarget(new URLSearchParams('meal=7'))).toBeNull()
  expect(returnTarget(new URLSearchParams('day=2026-10-13'))).toBeNull()
})

it('targets "/" when the day is today', () => {
  expect(returnTarget(new URLSearchParams('meal=7&day=2026-10-13'))).toEqual({
    path: '/',
    mealId: 7,
    day: '2026-10-13',
  })
})

it('targets "/day/<day>" for a different day', () => {
  expect(returnTarget(new URLSearchParams('meal=7&day=2026-10-14'))).toEqual({
    path: '/day/2026-10-14',
    mealId: 7,
    day: '2026-10-14',
  })
})
