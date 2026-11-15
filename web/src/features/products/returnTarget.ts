import { localDay } from '../../lib/dates'
import type { IsoDay } from '../../lib/types'

/**
 * Where to go after a successful create/import. With `?meal=<id>&day=<day>` on the URL
 * (set by AddItemSheet's "Create product" link, or carried the same way into the OFF
 * import path), returns to that day with the add-item sheet reopened on the new product.
 * Without them, the caller keeps the old behaviour: straight to the product detail page.
 */
export function returnTarget(
  params: URLSearchParams,
): { path: string; mealId: number; day: IsoDay } | null {
  const mealParam = params.get('meal')
  const day = params.get('day')
  if (!mealParam || !day) return null
  const mealId = Number(mealParam)
  if (!Number.isFinite(mealId)) return null
  const path = day === localDay() ? '/' : `/day/${day}`
  return { path, mealId, day: day as IsoDay }
}
