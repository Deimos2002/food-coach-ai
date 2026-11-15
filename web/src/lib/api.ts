// Typed fetch client. Bodies and responses carry decimals as strings; nothing here does arithmetic.
import { getTokenProvider } from './auth'
import type {
  AddItemPayload,
  Alias,
  DayView,
  DeleteResult,
  ExportResult,
  IsoDay,
  ItemResult,
  MealType,
  OffProduct,
  OverrideIn,
  PatchItemPayload,
  Product,
  ProductIn,
  Profile,
  RecentItem,
  SavedMeal,
  SavedMealIn,
  SearchHit,
  MealTemplate,
  Targets,
  Unit,
  UseSavedMealResult,
  WeightEntry,
  WeightTrendPoint,
  Dec,
  DayStatus,
  GuidelineCard,
  WeekResponse,
  CoachResponse,
  CoachFeedback,
  CoachFeedbackValue,
  CoachOpsResponse,
} from './types'

export class ApiError extends Error {
  status: number
  detail: string
  body: unknown

  constructor(status: number, detail: string, body: unknown = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
type QueryValue = string | number | boolean | null | undefined

export interface FetchOpts {
  method?: Method
  body?: unknown
  query?: Record<string, QueryValue>
  idempotencyKey?: string
}

const DEFAULT_API_URL = 'http://localhost:8000'

/** Falls back to the stage-1 default dev backend, never to the page's own origin. */
export function apiBase(): string {
  return import.meta.env.VITE_API_URL ?? DEFAULT_API_URL
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const url = new URL(path, apiBase())
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
  }
  return url.toString()
}

/** FastAPI sends `detail` as a string, or as a list of validation errors on a 422. */
function detailOf(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((e) => (typeof (e as { msg?: unknown })?.msg === 'string' ? (e as { msg: string }).msg : null))
      .filter((m): m is string => m !== null)
    if (msgs.length > 0) return msgs.join('; ')
  }
  return `HTTP ${status}`
}

export async function apiFetch<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const token = await getTokenProvider().getToken()
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey

  const res = await fetch(buildUrl(path, opts.query), {
    method: opts.method ?? 'GET',
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })

  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }
  if (res.status === 401) throw new ApiError(401, 'Signed out', body)
  if (!res.ok) throw new ApiError(res.status, detailOf(body, res.status), body)
  return body as T
}

const RETRY_DELAYS_MS = [500, 1500]
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const enc = encodeURIComponent

// --- Days and meals ----------------------------------------------------------------------

export const getDay = (day: IsoDay) => apiFetch<DayView>(`/days/${day}`)

export const createMeal = (day: IsoDay, meal_type: MealType) =>
  apiFetch<{ meal_id: number }>('/meals', { method: 'POST', body: { day, meal_type } })

/**
 * The only call that retries: up to twice, only when the network fails (fetch throws a
 * TypeError), always with the same Idempotency-Key. Never on an HTTP status.
 */
export async function addItem(mealId: number, payload: AddItemPayload, key: string): Promise<ItemResult> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await apiFetch<ItemResult>(`/meals/${mealId}/items`, {
        method: 'POST',
        body: payload,
        idempotencyKey: key,
      })
    } catch (err) {
      if (!(err instanceof TypeError) || attempt >= RETRY_DELAYS_MS.length) throw err
      await sleep(RETRY_DELAYS_MS[attempt] ?? 0)
    }
  }
}

export const patchItem = (itemId: number, payload: PatchItemPayload) =>
  apiFetch<ItemResult>(`/meal-items/${itemId}`, { method: 'PATCH', body: payload })

export const deleteItem = (itemId: number) => apiFetch<null>(`/meal-items/${itemId}`, { method: 'DELETE' })

export const moveItem = (itemId: number, mealId: number) =>
  apiFetch<{ item_id: number; meal_id: number }>(`/meal-items/${itemId}/move`, {
    method: 'POST',
    body: { meal_id: mealId },
  })

export const setSkipped = (mealId: number, skipped: boolean) =>
  apiFetch<{ meal_id: number; skipped: boolean }>(`/meals/${mealId}/skipped`, {
    method: 'PUT',
    body: { skipped },
  })

export const setDayStatus = (day: IsoDay, status: Exclude<DayStatus, 'not_logged'>) =>
  apiFetch<{ day: IsoDay; status: DayStatus }>(`/days/${day}/status`, { method: 'PUT', body: { status } })

// --- Search --------------------------------------------------------------------------------

export const search = (q: string) => apiFetch<SearchHit[]>('/search', { query: { q } })

export const getMealTemplate = (id: number) => apiFetch<MealTemplate>(`/meal-templates/${id}`)

export const recentItems = (limit = 20) => apiFetch<RecentItem[]>('/recent-items', { query: { limit } })

// --- Products ----------------------------------------------------------------------------

export const getProduct = (id: number) => apiFetch<Product>(`/products/${id}`)

export const setCookingAdditions = (productId: number, value: 'yes' | 'no' | 'unanswered') =>
  apiFetch<{ product_id: number; includes_cooking_additions: 'yes' | 'no' | 'unanswered' }>(
    `/products/${productId}/cooking-additions`, { method: 'PUT', body: { value } },
  )

export const createProduct = (body: ProductIn) =>
  apiFetch<{ product_id: number }>('/products', { method: 'POST', body })

/** A 409 carries `{detail: "needs basis", needs_basis: true}` in `ApiError.body`. */
export const importOff = (barcode: string, basis?: Unit) =>
  apiFetch<{ product_id: number }>(`/products/import-off/${enc(barcode)}`, {
    method: 'POST',
    query: { basis },
  })

export const offSearch = (q: string) => apiFetch<OffProduct[]>('/off/search', { query: { q } })

export const acceptRevision = (productId: number, revisionId: number) =>
  apiFetch<{ product_id: number; revision_id: number; status: 'adopted' }>(
    `/products/${productId}/revisions/${revisionId}/accept`,
    { method: 'POST' },
  )

export const createOverride = (productId: number, body: OverrideIn) =>
  apiFetch<{ override_id: number }>(`/products/${productId}/overrides`, { method: 'POST', body })

export const reconfirmOverride = (overrideId: number) =>
  apiFetch<{ override_id: number; pinned_revision_id: number }>(`/overrides/${overrideId}/reconfirm`, {
    method: 'POST',
  })

// --- Weight and profile ------------------------------------------------------------------

/** A 409 means an entry exists for that day; ask, then call again with `replace = true`. */
export const putWeight = (day: IsoDay, kg: Dec, replace = false) =>
  apiFetch<WeightEntry>(`/weights/${day}`, { method: 'PUT', body: { kg }, query: { replace } })

export const deleteWeight = (day: IsoDay) => apiFetch<null>(`/weights/${day}`, { method: 'DELETE' })

export const confirmWeight = (day: IsoDay) =>
  apiFetch<{ day: IsoDay; confirmed: true }>(`/weights/${day}/confirm`, { method: 'POST' })

export const weightTrend = () => apiFetch<WeightTrendPoint[]>('/weights/trend')

/** 404 when no profile is set. */
export const getProfile = () => apiFetch<Profile>('/profile')

export const putProfile = (body: Profile) => apiFetch<Profile>('/profile', { method: 'PUT', body })

/** 404 when no profile or no weight on or before `day`. */
export const getTargets = (day: IsoDay) => apiFetch<Targets>('/targets', { query: { day } })

// --- Saved meals and aliases -------------------------------------------------------------

export const listSavedMeals = () => apiFetch<SavedMeal[]>('/saved-meals')

export const createSavedMeal = (body: SavedMealIn) =>
  apiFetch<{ saved_meal_id: number }>('/saved-meals', { method: 'POST', body })

export const useSavedMeal = (id: number, mealId: number, key: string) =>
  apiFetch<UseSavedMealResult>(`/saved-meals/${id}/use`, {
    method: 'POST',
    body: { meal_id: mealId },
    idempotencyKey: key,
  })

export const listAliases = () => apiFetch<Alias[]>('/aliases')

export const createAlias = (text: string, productId: number) =>
  apiFetch<{ alias_id: number }>('/aliases', { method: 'POST', body: { text, product_id: productId } })

export const updateAlias = (id: number, text: string, productId: number) =>
  apiFetch<{ alias_id: number }>(`/aliases/${id}`, { method: 'PUT', body: { text, product_id: productId } })

// --- Data ----------------------------------------------------------------------------------

// --- Week and cards --------------------------------------------------------------------

export const getWeek = (isoWeek: string) => apiFetch<WeekResponse>(`/week/${enc(isoWeek)}`)

export const listCards = () => apiFetch<GuidelineCard[]>('/cards')

export const verifyCard = (id: string) => apiFetch<GuidelineCard>(`/cards/${enc(id)}/verify`, { method: 'POST' })

export const unverifyCard = (id: string) => apiFetch<GuidelineCard>(`/cards/${enc(id)}/unverify`, { method: 'POST' })

export const getCoachReview = (isoWeek: string) => apiFetch<CoachResponse>(`/coach/weeks/${enc(isoWeek)}`)

export const generateCoachReview = (isoWeek: string, regenerate = false) =>
  apiFetch<CoachResponse>(`/coach/weeks/${enc(isoWeek)}`, { method: 'POST', query: { regenerate } })

export const sendCoachFeedback = (reviewId: number, suggestionIndex: number, value: CoachFeedbackValue) =>
  apiFetch<CoachFeedback>(`/coach/reviews/${reviewId}/feedback`, {
    method: 'POST', body: { suggestion_index: suggestionIndex, value },
  })

export const getCoachOps = (month: string) => apiFetch<CoachOpsResponse>('/coach/ops', { query: { month } })

export const exportData = () => apiFetch<ExportResult>('/export')

export const deleteAllData = () => apiFetch<DeleteResult>('/me/data', { method: 'DELETE' })
