// The only module allowed to turn decimal strings into numbers, and only for display.
import type { Dec, Quantity } from './types'

const ZERO_DECIMAL_UNITS = new Set(['kcal', 'mg'])

export function digitsFor(unit: string): number {
  return ZERO_DECIMAL_UNITS.has(unit) ? 0 : 1
}

export function formatDec(d: Dec | null, digits = 1): string {
  if (d === null) return '?'
  const n = Number(d)
  if (!Number.isFinite(n)) return '?'
  const fmt = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
  // A known nonzero value must never look like a known zero after rounding.
  if (n !== 0 && Math.abs(n) < 0.5 * 10 ** -digits) return `< ${fmt.format(10 ** -digits)}`
  return fmt.format(n)
}

/**
 * Normalises a decimal string for exact comparison only (never for sending/display): strips
 * trailing zeros after a decimal point, then a trailing bare point, so "100.0" reads as equal
 * to "100". String manipulation only — no Number()/parseFloat.
 */
function normDec(s: Dec): string {
  const v = s.trim().replaceAll(',', '.')
  if (!v.includes('.')) return v
  return v.replace(/0+$/, '').replace(/\.$/, '')
}

/** True only when the two decimal strings denote exactly the same value. */
function sameDecimal(a: Dec, b: Dec): boolean {
  return normDec(a) === normDec(b)
}

/**
 * lower null: "?"; upper null: "≥ lower"; exactly equal: one value; else "lower–upper".
 * Bounds are compared with string ops, never Number(), so unequal decimals never collapse
 * just because they round to the same text at the default digit count. If increasing digits
 * (up to 2 extra) still can't tell them apart, renders "≈ lower–upper" instead of one value.
 */
export function formatRange(lower: Dec | null, upper: Dec | null, unit: string): string {
  if (lower === null) return '?'
  const digits = digitsFor(unit)
  const lo = formatDec(lower, digits)
  if (upper === null) return lo.startsWith('<') ? `> 0 ${unit}` : `≥ ${lo} ${unit}`
  if (sameDecimal(lower, upper)) return `${lo} ${unit}`
  const hi = formatDec(upper, digits)
  if (lo !== hi) return `${lo}–${hi} ${unit}`
  for (let extra = 1; extra <= 2; extra++) {
    const d = digits + extra
    const loX = formatDec(lower, d)
    const hiX = formatDec(upper, d)
    if (loX !== hiX) return `${loX}–${hiX} ${unit}`
  }
  return `≈ ${lo}–${hi} ${unit}`
}

/**
 * A share decimal string ("0.38") scaled ×100 for percentage display only. Never used for
 * comparison or sent back to the server — formatRange still does the actual rendering.
 */
export function sharePercent(d: Dec | null): Dec | null {
  if (d === null) return null
  const n = Number(d)
  if (!Number.isFinite(n)) return null
  return String(n * 100)
}

/** `formatRange` over a contributor's `[lower, upper]` share, scaled to a percentage. */
export function formatShareRange(share: [Dec, Dec | null]): string {
  return formatRange(sharePercent(share[0]), sharePercent(share[1]), '%')
}

export function formatQuantity(q: Quantity): string {
  if (q.kind === 'estimated_range') return formatRange(q.lower, q.upper, q.unit)
  return formatRange(q.value, q.value, q.unit)
}

/** Display-only SVG coordinates. Never used for stored weights, trends or targets. */
export function sparklinePoints(values: Dec[]): string {
  const numbers = values.map((value) => Number(value))
  if (!numbers.length || numbers.some((value) => !Number.isFinite(value))) return ''
  const low = Math.min(...numbers)
  const high = Math.max(...numbers)
  return numbers.map((value, index) => {
    const x = numbers.length === 1 ? 150 : 10 + index * 280 / (numbers.length - 1)
    const y = high === low ? 30 : 50 - (value - low) * 40 / (high - low)
    return `${x},${y}`
  }).join(' ')
}

/**
 * Display-only total of ingredient values after the user changes their grams: each part's
 * lower/upper is scaled by grams/baseGrams and summed. Unknown (null lower) in any part makes
 * the total unknown; a null upper makes it open-ended; a non-positive base or grams value
 * makes it unknown. Never sent back to the server.
 * shortcut: Number maths for display only, may round differently from the server; upgrade if totals are ever stored
 */
export function scaledTotal(
  parts: { lower: Dec | null; upper: Dec | null; grams: Dec; baseGrams: Dec }[],
): { lower: Dec | null; upper: Dec | null } {
  let lo = 0
  let hi: number | null = 0
  for (const p of parts) {
    if (p.lower === null) return { lower: null, upper: null }
    const k = Number(p.grams) / Number(p.baseGrams)
    if (!(Number(p.baseGrams) > 0) || !Number.isFinite(k)) return { lower: null, upper: null }
    lo += Number(p.lower) * k
    hi = p.upper === null || hi === null ? null : hi + Number(p.upper) * k
  }
  return { lower: String(lo), upper: hi === null ? null : String(hi) }
}

/** Display-only exact total; an open range or unresolved contribution is unknown. */
export function nutrientDisplayValue(state: import('./types').NutrientState | undefined, digits?: number): number | null {
  if (!state || state.unresolved.length || state.unquantified.length || state.additions_undeclared.length || state.recorded.lower === null || state.recorded.upper === null || !sameDecimal(state.recorded.lower, state.recorded.upper)) return null
  const value = Number(state.recorded.lower)
  if (!Number.isFinite(value)) return null
  return digits === undefined ? value : Number(value.toFixed(digits))
}

/** Display-only average; missing days never silently disappear from the denominator. */
export function progressAverage(values: (number | null)[]): Dec | null {
  return !values.length || values.some(value => value === null) ? null : String(values.reduce<number>((sum, value) => sum + (value ?? 0), 0) / values.length)
}

/** Display-only change in the smoothed trend, never sent to the API. */
export function trendChange(first: Dec | undefined, last: Dec | undefined): Dec | null {
  if (first === undefined || last === undefined) return null
  const change = Number(last) - Number(first)
  return Number.isFinite(change) ? String(change) : null
}
