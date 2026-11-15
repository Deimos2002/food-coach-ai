import { expect, it } from 'vitest'
import { progressAverage, trendChange } from './format'
it('progress average requires every day and preserves known zero', () => {
  expect(progressAverage([100, null])).toBeNull()
  expect(progressAverage([])).toBeNull()
  expect(progressAverage([0, 200])).toBe('100')
})
it('trend change is unknown without both endpoints', () => {
  expect(trendChange(undefined, '72')).toBeNull()
  expect(trendChange('73', '72.5')).toBe('-0.5')
})
