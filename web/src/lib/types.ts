// API types, snake_case, matching the backend's JSON exactly.
// Decimals travel as strings (`Dec`) and are never parsed for arithmetic in the browser.

export type Dec = string
export type IsoDay = string // YYYY-MM-DD, the user's local calendar date
export type IsoDateTime = string
export type Unit = 'g' | 'ml'
export type QuantitySource = 'weighed' | 'package' | 'label_portion' | 'user_estimate' | 'photo_estimate'
export type QuantityKind = 'recorded' | 'estimated_range'
export type MealType = 'breakfast' | 'lunch' | 'dinner' | 'snack'
export type DayStatus = 'not_logged' | 'partial' | 'complete'
export type Nutrient =
  | 'energy_kcal'
  | 'protein'
  | 'carbohydrate'
  | 'sugars'
  | 'fat'
  | 'saturated_fat'
  | 'fibre'
  | 'salt'
  | 'caffeine'
  | 'alcohol'
  | 'polyols'
  | 'erythritol'
export type Qualifier = 'none' | 'trace' | 'less_than'
export type ValueSource = 'label' | 'ciqual' | 'off_contributor' | 'manual'
export type Derivation = 'direct' | 'converted' | 'computed'
export type Verification = 'unverified' | 'user_verified' | 'flagged'

// --- Days and meals -------------------------------------------------------------------

export interface Quantity {
  kind: QuantityKind
  value: Dec
  lower: Dec | null
  upper: Dec | null
  unit: Unit
  source: QuantitySource
}

/** One nutrient on one logged item. Unknown: lower/upper null. Known with upper null: open bound. */
export interface ItemNutrient {
  known: boolean
  lower: Dec | null
  upper: Dec | null
  qualifier: Qualifier
}

export interface ItemView {
  item_id: number
  name: string
  requested_product_id: number
  resolved_product_id: number
  profile_revision_id: number
  quantity: Quantity
  nutrients: Partial<Record<Nutrient, ItemNutrient>>
  logged_at: IsoDateTime
  consumed_at: IsoDateTime | null
  /** Set when the item was added from a meal template; items sharing a dish group in the day view. */
  dish?: string | null
}

export interface MealView {
  meal_id: number
  meal_type: MealType
  skipped: boolean
  items: ItemView[]
}

export interface NutrientState {
  recorded: { lower: Dec | null; upper: Dec | null }
  unresolved: string[]
  unquantified: string[]
  additions_undeclared: string[]
  fields_complete: boolean
  log_complete: boolean
}

export interface DayView {
  day: IsoDay
  status: DayStatus
  completed_at: IsoDateTime | null
  meals: MealView[]
  nutrients: Record<Nutrient, NutrientState>
}

export interface AddItemPayload {
  product_id: number
  amount: Dec
  lower?: Dec | null
  upper?: Dec | null
  unit: Unit
  quantity_source: QuantitySource
  consumed_at?: IsoDateTime | null
  dish?: string
}

export type PatchItemPayload = Partial<Omit<AddItemPayload, 'product_id'>>

export interface ItemResult {
  item_id: number
  nutrients: Partial<Record<Nutrient, ItemNutrient>>
}

// --- Search -----------------------------------------------------------------------------

export interface Portion {
  label: string
  amount: Dec
  kind: string
}

export interface SearchResult {
  type?: 'product'
  product_id: number
  name: string
  name_en?: string | null
  brand: string | null
  is_fast_food?: boolean
  basis_unit: Unit
  portions: Portion[]
  kcal_per_100: Dec | null
}

export interface MealResult {
  type: 'meal'
  template_id: number
  name: string
  name_en: string | null
  cuisine: string | null
  chain: string | null
  estimate: boolean
}

export type SearchHit = SearchResult | MealResult

/** A nutrient value on a template ingredient; same shape as the backend's item nutrient JSON. */
export interface TemplateNutrient {
  known: boolean
  lower: Dec | null
  upper: Dec | null
}

export interface MealTemplate {
  id: number
  name: string
  name_en: string | null
  cuisine: string | null
  chain: string | null
  estimate: boolean
  source_url: string | null
  items: {
    product_id: number
    name: string
    name_en: string | null
    grams: Dec
    nutrients: Partial<Record<Nutrient, TemplateNutrient>>
  }[]
  totals: Partial<Record<Nutrient, TemplateNutrient>>
}

export interface RecentItem {
  product_id: number
  name: string
  brand: string | null
  basis_unit: Unit
  last_amount: Dec
  last_unit: Unit
  last_logged_at: IsoDateTime
}

/** Raw Open Food Facts search hit, passed through unchanged from OFF. */
export type OffProduct = Record<string, unknown> & { code?: string; product_name?: string }

// --- Products ---------------------------------------------------------------------------

export interface ProductValue {
  known: boolean
  lower: Dec | null
  upper: Dec | null
  qualifier: Qualifier
  // Present only when known.
  source?: ValueSource
  derivation?: Derivation
  verification?: Verification
}

export interface ProductRevision {
  id: number
  status: string
  created_at: IsoDateTime
}

export interface ProductOverride {
  id: number
  version: number
  nutrient: Nutrient
  value: ProductValue
  pinned_revision_id: number
  retain_across_revisions: boolean
  needs_reconfirm: boolean
}

export interface Product {
  product_id: number
  owner: 'user' | 'catalogue'
  name: string
  brand: string | null
  basis_unit: Unit
  kind: 'product' | 'dish'
  includes_cooking_additions: 'yes' | 'no' | 'unanswered' | null
  values: Record<Nutrient, ProductValue>
  portions: Portion[]
  revisions: ProductRevision[]
  pending_reviews: number[]
  overrides: ProductOverride[]
}

/** A point `{value}` or a range `{lower, upper}` (upper null = open). */
export type ValueIn = { value: Dec } | { lower: Dec; upper: Dec | null }

export interface ProductIn {
  kind: 'product' | 'dish'
  includes_cooking_additions?: 'yes' | 'no' | 'unanswered' | null
  name: string
  brand?: string | null
  basis_unit: Unit
  state?: string
  density_g_per_ml?: Dec | null
  density_source?: string | null
  values?: Partial<Record<Nutrient, ValueIn | null>>
}

export interface OverrideIn {
  nutrient: Nutrient
  value: ValueIn | null
  retain_across_revisions?: boolean
  reason?: string
}

// --- Weight, profile, targets -----------------------------------------------------------

export interface WeightEntry {
  day: IsoDay
  kg: Dec
}

export interface WeightTrendPoint {
  day: IsoDay
  trend: Dec
  flagged: boolean
  suggest_confirm: boolean
}

export type Sex = 'male' | 'female'
export type Goal = 'lose' | 'maintain' | 'gain'

export interface Profile {
  sex: Sex
  birth_year: number
  height_cm: Dec
  activity: Dec // one of "1.2" | "1.375" | "1.55" | "1.725"
  goal: Goal
  pace_pct_per_week: Dec
}

/** Whole numbers: the backend sends these as JSON integers, not decimal strings. */
export interface Targets {
  maintenance_kcal: number
  kcal: number
  protein_g: number
}

// --- Saved meals and aliases -----------------------------------------------------------

export interface SavedMealItem {
  product_id: number
  name: string
  amount: Dec
  unit: Unit
}

export interface SavedMeal {
  id: number
  name: string
  items: SavedMealItem[]
}

export interface SavedMealIn {
  name: string
  items: { product_id: number; amount: Dec; unit: Unit }[]
}

export interface BlockedSavedItem {
  product_id: number
  reason: 'retired' | 'unit changed'
  successors: number[]
}

export interface UseSavedMealResult {
  added: number[]
  blocked: BlockedSavedItem[]
}

export interface Alias {
  id: number
  text: string
  product_id: number
  suspended: boolean
}

// --- Data --------------------------------------------------------------------------------

export interface ExportResult {
  generation: number
  exported_at: IsoDateTime
  data: Record<string, unknown[]>
}

export interface DeleteResult {
  generation: number
  deleted: Record<string, number>
}

// --- Week and cards ----------------------------------------------------------------------

export type IsoWeekStr = string // YYYY-Www

export interface GuidelineCard {
  id: string
  source_org: string
  title: string
  quote: string
  url: string
  retrieved_on: string
  threshold: Dec | null
  unit: string | null
  notes: string | null
  verified_by_user_at: IsoDateTime | null
}

export interface ItemRef {
  day: IsoDay
  meal_id: number
  item_id: number
}

export interface FactContributor {
  id: string
  name: string
  /** `[lower, upper]` fractions of the fact total ("0.38" = 38%), upper null meaning open-ended. Not scaled: use `formatShareRange` for display. */
  share: [Dec, Dec | null]
  additions_undeclared: boolean
  item_refs: ItemRef[]
}

export interface FactSwap {
  id: string
  for: string
  name: string
  /** Present only when the backend resolved this swap to a live product. */
  product_id?: number
  values: {
    from_lower: Dec
    to_upper: Dec
    unit: string
    basis: string
  }
}

export interface Fact {
  id: string
  rule: string
  claim: string
  values: Record<string, string>
  days: IsoDay[]
  card_id: string | null
  message: string
  contributors: FactContributor[]
  unknown_for: string[]
  additions_undeclared: string[]
  swaps: FactSwap[]
}

export interface FactSheet {
  schema_version: number
  week: IsoWeekStr
  coverage: string
  days_included: IsoDay[]
  days_missing: IsoDay[]
  facts: Fact[]
  goal: { type: string; pace_pct_per_week: Dec } | null
  trend: { kg_start: Dec; kg_end: Dec } | null
  hash: string
}

export interface WeekResponse {
  fact_sheet: FactSheet
  complete_days: number
  cards: GuidelineCard[]
}

// --- Coach: matches routes_coach.py and coach/service.py -------------------------------

export interface CoachSuggestion {
  kind: 'swap' | 'habit' | 'keep_going'
  title: string
  body: string
  fact_ids: string[]
  swap_ids: string[]
  /** Guideline passages the advice is based on; absent on older and template reviews. */
  sources?: { title: string; url: string; locator: string }[]
}

export interface CoachReview {
  id: number
  iso_week: IsoWeekStr
  generation_no: number
  fact_sheet: FactSheet
  suggestions: CoachSuggestion[]
  source: 'llm' | 'template'
  fallback_reason: string | null
  prompt_version: string
  created_at: IsoDateTime
}

export type CoachResponse =
  | { status: 'ok'; review: CoachReview; note?: string }
  | { status: 'nothing_to_suggest'; review: CoachReview; message: string }
  /** The model produced no review that passed the checker; there is no static fallback. */
  | { status: 'unavailable'; review: CoachReview; message: string }
  | { status: 'in_progress' }
  | { status: 'insufficient_data'; complete_days: number }

export type CoachFeedbackValue = 'up' | 'down' | 'will_try'

export interface CoachFeedback {
  id: number
  review_id: number
  suggestion_index: number
  value: CoachFeedbackValue
}

export interface CoachOpsResponse {
  calls: number
  spent_eur: Dec
  cap_eur: Dec | null
  first_try_pass_rate: Dec | null
  fallback_rate: Dec | null
  p50_ms: number | null
  p95_ms: number | null
}
