import { describe, expect, it } from 'vitest'
import { addDays, localDay } from './dates'

describe('localDay', () => {
  it('uses the local calendar date at 00:30', () => {
    expect(localDay(new Date(2026, 9, 13, 0, 30))).toBe('2026-10-13')
  })
})

describe('addDays', () => {
  it('crosses month ends', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })
})
