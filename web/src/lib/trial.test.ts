import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { _resetForTests, startTimer, stats, stopTimer } from './trial'

describe('trial', () => {
  beforeEach(() => {
    localStorage.clear()
    _resetForTests()
  })
  afterEach(() => vi.restoreAllMocks())

  it('cold_and_warm_medians_kept_separate', () => {
    const now = vi.spyOn(performance, 'now')
    const run = (start: number, end: number) => {
      now.mockReturnValueOnce(start)
      const t = startTimer()
      now.mockReturnValueOnce(end)
      stopTimer(t)
    }
    run(0, 9000) // cold
    run(0, 1000) // warm
    run(0, 3000) // warm
    run(0, 2000) // warm
    expect(stats()).toEqual({ cold: { n: 1, medianMs: 9000 }, warm: { n: 3, medianMs: 2000 } })
    const saved = JSON.parse(localStorage.getItem('trial.v1') ?? '[]') as { kind: string; ms: number; at: string }[]
    expect(saved.map((r) => r.kind)).toEqual(['cold', 'warm', 'warm', 'warm'])
    expect(typeof saved[0]?.at).toBe('string')
  })

  it('empty stats have null medians', () => {
    expect(stats()).toEqual({ cold: { n: 0, medianMs: null }, warm: { n: 0, medianMs: null } })
  })

  it('storage_failure_is_ignored', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => stopTimer(startTimer())).not.toThrow()
    expect(stats()).toEqual({ cold: { n: 0, medianMs: null }, warm: { n: 0, medianMs: null } })
  })
})
