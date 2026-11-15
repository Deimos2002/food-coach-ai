import type { IsoDay } from './types'

const pad = (n: number) => String(n).padStart(2, '0')

/** The user's local calendar date as YYYY-MM-DD (never UTC). */
export function localDay(d: Date = new Date()): IsoDay {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Calendar arithmetic on a YYYY-MM-DD day (dates only, never nutrients). */
export function addDays(day: IsoDay, n: number): IsoDay {
  const [y = NaN, m = NaN, d = NaN] = day.split('-').map((s) => parseInt(s, 10))
  return localDay(new Date(y, m - 1, d + n))
}

export type IsoWeek = string // YYYY-Www

const WEEK_RE = /^(\d{4})-W(\d{2})$/

/** ISO-8601 week ("YYYY-Www") of a YYYY-MM-DD local day. Calendar arithmetic only. */
export function isoWeekOf(day: IsoDay): IsoWeek {
  const [y = NaN, m = NaN, d = NaN] = day.split('-').map((s) => parseInt(s, 10))
  const date = new Date(y, m - 1, d)
  // ISO weekday: Monday=1 ... Sunday=7.
  const isoDow = date.getDay() === 0 ? 7 : date.getDay()
  // Move to the Thursday of this ISO week; the ISO year is that Thursday's calendar year.
  date.setDate(date.getDate() + 4 - isoDow)
  const isoYear = date.getFullYear()
  const yearStart = new Date(isoYear, 0, 1)
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  return `${isoYear}-W${pad(week)}`
}

/** Monday of a "YYYY-Www" ISO week, as a YYYY-MM-DD local day. */
function mondayOf(isoWeek: IsoWeek): IsoDay {
  const m = WEEK_RE.exec(isoWeek)
  if (!m) return isoWeek
  const year = parseInt(m[1] ?? '', 10)
  const week = parseInt(m[2] ?? '', 10)
  // Jan 4th always falls in ISO week 1.
  const jan4 = new Date(year, 0, 4)
  const jan4Dow = jan4.getDay() === 0 ? 7 : jan4.getDay()
  const week1Monday = new Date(year, 0, 4 - (jan4Dow - 1))
  const monday = new Date(week1Monday)
  monday.setDate(monday.getDate() + (week - 1) * 7)
  return localDay(monday)
}

/** `n` ISO weeks after (or, if negative, before) `isoWeek`. Handles year boundaries. */
export function addWeeks(isoWeek: IsoWeek, n: number): IsoWeek {
  return isoWeekOf(addDays(mondayOf(isoWeek), n * 7))
}
