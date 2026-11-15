import { describe, expect, it } from 'vitest'
import { formatDec, formatQuantity, formatRange, scaledTotal } from './format'

describe('formatRange', () => {
  it('renders equal bounds as one value', () => {
    expect(formatRange('12.5', '12.5', 'g')).toBe('12,5 g')
  })
  it('renders exactly-equal decimal values that differ only in trailing zeros as one value', () => {
    expect(formatRange('100', '100.0', 'g')).toBe('100 g')
  })
  it('does not collapse unequal bounds that round to the same text', () => {
    expect(formatRange('100.1', '100.4', 'kcal')).toBe('100,1–100,4 kcal')
  })
  it('renders ≈ lo–hi when bounds still round to the same text after extra digits', () => {
    expect(formatRange('100.001', '100.002', 'kcal')).toBe('≈ 100–100 kcal')
  })
  it('renders a range', () => {
    expect(formatRange('10', '14', 'g')).toBe('10–14 g')
  })
  it('renders an open upper bound as ≥', () => {
    expect(formatRange('80', null, 'mg')).toBe('≥ 80 mg')
  })
  it('renders unknown as ?', () => {
    expect(formatRange(null, null, 'g')).toBe('?')
  })
  it('uses 0 decimals for kcal', () => {
    expect(formatRange('370.5', '370.5', 'kcal')).toBe('371 kcal')
  })
})

describe('known nonzero never displays as 0', () => {
  it('shows < 0,1 for a tiny gram value', () => {
    expect(formatRange('0.04', '0.04', 'g')).toBe('< 0,1 g')
  })
  it('shows < 1 for tiny kcal and mg', () => {
    expect(formatRange('0.4', '0.4', 'kcal')).toBe('< 1 kcal')
    expect(formatRange('0.2', '0.2', 'mg')).toBe('< 1 mg')
  })
  it('open bound with tiny lower shows > 0', () => {
    expect(formatRange('0.2', null, 'mg')).toBe('> 0 mg')
  })
  it('keeps a true 0 as 0', () => {
    expect(formatRange('0', '0', 'g')).toBe('0 g')
    expect(formatRange('0.00', '0.00', 'kcal')).toBe('0 kcal')
    expect(formatRange('0', null, 'g')).toBe('≥ 0 g')
  })
  it('range with tiny upper', () => {
    expect(formatRange('0', '0.04', 'g')).toBe('0–< 0,1 g')
  })
  it('formatDec marks tiny nonzero values', () => {
    expect(formatDec('0.04')).toBe('< 0,1')
    expect(formatDec('0.3', 0)).toBe('< 1')
  })
})

describe('formatDec', () => {
  it('renders null as ?', () => {
    expect(formatDec(null)).toBe('?')
  })
  it('keeps a known zero as 0', () => {
    expect(formatDec('0')).toBe('0')
  })
})

describe('formatQuantity', () => {
  it('renders a recorded quantity', () => {
    expect(
      formatQuantity({ kind: 'recorded', value: '150', lower: '150', upper: '150', unit: 'g', source: 'weighed' }),
    ).toBe('150 g')
  })
  it('renders an estimated range', () => {
    expect(
      formatQuantity({ kind: 'estimated_range', value: '120', lower: '100', upper: '140', unit: 'g', source: 'user_estimate' }),
    ).toBe('100–140 g')
  })
})

describe('scaledTotal', () => {
  const part = (lower: string | null, upper: string | null, grams = '100', baseGrams = '100') => ({
    lower, upper, grams, baseGrams,
  })
  const show = (parts: ReturnType<typeof part>[]) => {
    const t = scaledTotal(parts)
    return formatRange(t.lower, t.upper, 'kcal')
  }
  it('scales and sums', () => {
    expect(show([part('200', '200', '50'), part('300', '300', '150', '150')])).toBe('400 kcal')
  })
  it('an unknown part makes the total unknown', () => {
    expect(show([part('200', '200'), part(null, null)])).toBe('?')
  })
  it('a null upper makes the total open-ended', () => {
    expect(show([part('200', '200'), part('100', null)])).toBe('≥ 300 kcal')
  })
  it('a zero base is unknown', () => {
    expect(show([part('200', '200', '50', '0')])).toBe('?')
  })
})
