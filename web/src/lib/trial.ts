// Usage trial (spec §10.1): time from opening "add item" to a successful add, kept on the device.
// Timings are durations in ms, not nutrient values, so plain number maths is fine here.

export type TrialKind = 'cold' | 'warm'
export interface TrialRun {
  ms: number
  kind: TrialKind
  at: string
}
export interface TrialStats {
  cold: { n: number; medianMs: number | null }
  warm: { n: number; medianMs: number | null }
}

const STORAGE_KEY = 'trial.v1'
let warmed = false // false until the first successful add since page load

export function _resetForTests(): void {
  warmed = false
}

export function startTimer(): number {
  return performance.now()
}

function load(): TrialRun[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(parsed) ? (parsed as TrialRun[]) : []
  } catch {
    return []
  }
}

/** Records one successful add. The first since page load is "cold", later ones "warm". */
export function stopTimer(start: number): TrialRun {
  const run: TrialRun = {
    ms: Math.round(performance.now() - start),
    kind: warmed ? 'warm' : 'cold',
    at: new Date().toISOString(),
  }
  warmed = true
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...load(), run]))
  } catch {
    // Storage blocked or full: the trial is best-effort and must never break logging.
  }
  return run
}

function median(xs: number[]): number | null {
  if (xs.length === 0) return null
  const s = [...xs].sort((a, b) => a - b)
  const mid = Math.floor(s.length / 2)
  return s.length % 2 === 1 ? (s[mid] ?? null) : ((s[mid - 1] ?? 0) + (s[mid] ?? 0)) / 2
}

export function stats(): TrialStats {
  const runs = load()
  const of = (kind: TrialKind) => {
    const ms = runs.filter((r) => r.kind === kind).map((r) => r.ms)
    return { n: ms.length, medianMs: median(ms) }
  }
  return { cold: of('cold'), warm: of('warm') }
}
