import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { createMeal, getDay, getProduct, getTargets, setDayStatus, setSkipped } from '../../lib/api'
import { localDay } from '../../lib/dates'
import type { DayStatus, ItemView, MealType } from '../../lib/types'
import { EditItemDialog } from './EditItemDialog'
import { MealCard } from './MealCard'
import { Button } from '../../ui/Button'
import { DateSwitcher } from '../../ui/DateSwitcher'
import { DaySummary } from './DaySummary'
import './day.css'
import { NutrientPanel } from './NutrientPanel'

const MEAL_TYPES: MealType[] = ['breakfast', 'lunch', 'dinner', 'snack']
const STATUS_LABEL: Record<DayStatus, string> = {
  not_logged: 'Not logged',
  partial: 'Partial',
  complete: 'Complete ✓',
}

export interface DayPageProps {
  /** Called with the meal to add to. Defaults to opening the full-page Add route for that meal. */
  onAdd?: (mealId: number) => void
}

export function DayPage({ onAdd: onAddProp }: DayPageProps) {
  const params = useParams<{ day: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  // On "/" (no :day param) the viewed day is "today" and must roll over at local midnight,
  // even if the tab has been open since before midnight. On "/day/:day" the day is fixed.
  const isToday = params.day === undefined
  const [today, setToday] = useState(localDay)
  useEffect(() => {
    if (!isToday) return
    const refresh = () => setToday(localDay())
    let timer: ReturnType<typeof setTimeout>
    const scheduleNextMidnight = () => {
      const now = new Date()
      const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 1)
      timer = setTimeout(() => {
        refresh()
        scheduleNextMidnight()
      }, next.getTime() - now.getTime())
    }
    scheduleNextMidnight()
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
  }, [isToday])

  const day = isToday ? today : params.day
  const [editingItem, setEditingItem] = useState<ItemView | null>(null)
  const onAdd = onAddProp ?? ((mealId: number) => navigate(`/add/${day}/${mealId}`))
  const qc = useQueryClient()
  const query = useQuery({ queryKey: ['day', day], queryFn: () => getDay(day) })
  const targets = useQuery({ queryKey: ['targets', day], queryFn: () => getTargets(day), retry: false })
  const invalidate = () => qc.invalidateQueries({ queryKey: ['day', day] })

  /**
   * Returning from "Create product" / OFF import with `{reopenMealId, productId}` in
   * location state (set by NewProductForm / OffImport via returnTarget): reopen
   * Add for that meal, pre-selected on the new product, instead of leaving the
   * user on a dead-end product detail page.
   */
  useEffect(() => {
    const state = location.state as { reopenMealId?: number; productId?: number } | null
    if (!state?.reopenMealId || !state.productId) return
    let active = true
    const { reopenMealId, productId } = state
    getProduct(productId)
      .then((p) => {
        if (!active) return
        navigate(`/add/${day}/${reopenMealId}`, { replace: true, state: { initialProduct: { product_id: p.product_id, name: p.name, basis_unit: p.basis_unit, portions: p.portions } } })
      })
      .catch(() => {
        if (!active) return
        navigate(`/add/${day}/${reopenMealId}`, { replace: true, state: { initialProduct: { product_id: productId, name: `Product #${productId}`, basis_unit: 'g', portions: [] } } })
      })
    navigate(location.pathname, { replace: true })
    return () => {
      active = false
    }
    // Runs once on mount only: deliberately not reacting to `location.state`, since the
    // `navigate(..., { replace: true })` above clears it and would otherwise re-trigger
    // this effect and cancel the in-flight getProduct before it resolves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * On "/", re-reads localDay() at the moment of the action. If it no longer matches the
   * rendered day (midnight passed since the last render/timer tick), refreshes instead of
   * acting on a meal id that belongs to the stale day.
   */
  const staleGuard = (): boolean => {
    if (!isToday) return false
    const now = localDay()
    if (now === day) return false
    setToday(now)
    return true
  }

  const guardedOnAdd = (mealId: number) => {
    if (staleGuard()) return
    onAdd(mealId)
  }

  const addMeal = useMutation({
    mutationFn: (mealType: MealType) => createMeal(day, mealType),
    onSuccess: (r) => onAdd(r.meal_id),
    onSettled: invalidate,
  })

  const guardedCreateMeal = (mealType: MealType) => {
    if (staleGuard()) return
    addMeal.mutate(mealType)
  }
  const status = useMutation({
    mutationFn: (s: 'complete' | 'partial') => setDayStatus(day, s),
    onSettled: invalidate,
  })
  const skip = useMutation({
    mutationFn: ({ mealId, skipped }: { mealId: number; skipped: boolean }) => setSkipped(mealId, skipped),
    onSettled: invalidate,
  })

  const data = query.data
  const error = query.error ?? addMeal.error ?? status.error ?? skip.error
  return (
    <div className="day-page">
      <header className="day-header"><h1>Today</h1>
        <DateSwitcher day={day} onChange={(value) => {
          setToday(localDay())
          navigate(value === localDay() ? '/' : `/day/${value}`)
        }} />
      </header>
      {error && <p role="alert">{error.message}</p>}
      {query.isPending && <p>Loading...</p>}
      {data && (
        <>
          <DaySummary nutrients={data.nutrients} targets={targets.data}>
          <p className="day-status">
            <span>{STATUS_LABEL[data.status]}</span>{' '}
            {data.status === 'complete' ? (
              <Button variant="secondary" type="button" disabled={status.isPending} onClick={() => status.mutate('partial')}>
                Reopen
              </Button>
            ) : (
              <Button variant="secondary" type="button" disabled={status.isPending} onClick={() => status.mutate('complete')}>
                Mark day complete
              </Button>
            )}
          </p>
          </DaySummary>
          <div className="day-meals">
          {MEAL_TYPES.map((mt) => {
            const meal = data.meals.find((m) => m.meal_type === mt)
            return (
              <MealCard
                key={mt}
                mealType={mt}
                meal={meal}
                day={day}
                addLink={!onAddProp}
                busy={addMeal.isPending || skip.isPending}
                onAdd={() => (meal ? guardedOnAdd(meal.meal_id) : guardedCreateMeal(mt))}
                onSetSkipped={(mealId, skipped) => skip.mutate({ mealId, skipped })}
                onEditItem={setEditingItem}
              />
            )
          })}
          </div>
          <NutrientPanel nutrients={data.nutrients} />
        </>
      )}
      {editingItem && <EditItemDialog item={editingItem} day={day} onClose={() => setEditingItem(null)} />}
    </div>
  )
}
