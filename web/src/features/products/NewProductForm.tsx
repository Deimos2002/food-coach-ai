import { Button, PageHeader } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createProduct } from '../../lib/api'
import type { Nutrient, ProductIn, Unit } from '../../lib/types'
import { OffImport } from './OffImport'
import { returnTarget } from './returnTarget'

const nutrients: [Nutrient, string][] = [
  ['energy_kcal', 'Energy (kcal)'], ['protein', 'Protein'], ['carbohydrate', 'Carbohydrate'],
  ['sugars', 'Sugars'], ['fat', 'Fat'], ['saturated_fat', 'Saturated fat'], ['fibre', 'Fibre'],
  ['salt', 'Salt'], ['caffeine', 'Caffeine'], ['alcohol', 'Alcohol'], ['polyols', 'Polyols'],
  ['erythritol', 'Erythritol'],
]

export function NewProductForm() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const cache = useQueryClient()
  const [name, setName] = useState('')
  const [brand, setBrand] = useState('')
  const [kind, setKind] = useState<'product' | 'dish'>('product')
  const [basis, setBasis] = useState<Unit>('g')
  const [additions, setAdditions] = useState<'yes' | 'no' | 'unanswered'>('unanswered')
  const [known, setKnown] = useState<Partial<Record<Nutrient, boolean>>>({})
  const [values, setValues] = useState<Partial<Record<Nutrient, string>>>({})
  const mutation = useMutation({
    mutationFn: createProduct,
    onSuccess: async ({ product_id }) => {
      await cache.invalidateQueries({ queryKey: ['search'] })
      const target = returnTarget(searchParams)
      if (target) {
        navigate(target.path, { state: { reopenMealId: target.mealId, productId: product_id } })
      } else {
        navigate(`/products/${product_id}`)
      }
    },
  })
  return <main className="restyle-page ui-card">
    <PageHeader title="New product" />
    <form className="restyle-form" onSubmit={(event) => {
      event.preventDefault()
      if (mutation.isPending) return
      const body: ProductIn = {
        name: name.trim(), brand: brand.trim() || null, kind, basis_unit: basis,
        values: Object.fromEntries(nutrients.map(([n]) => [n,
          known[n] && values[n]?.trim() ? { value: values[n]!.trim().replace(',', '.') } : null,
        ])),
        ...(kind === 'dish' ? { includes_cooking_additions: additions } : {}),
      }
      mutation.mutate(body)
    }}>
      <label>Name <input className="restyle-control" required value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label>Brand <input className="restyle-control" value={brand} onChange={(e) => setBrand(e.target.value)} /></label>
      <label>Kind <select className="restyle-control" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
        <option value="product">Product</option><option value="dish">Dish</option>
      </select></label>
      <label>Basis unit <select className="restyle-control" value={basis} onChange={(e) => setBasis(e.target.value as Unit)}>
        <option value="g">g</option><option value="ml">ml</option>
      </select></label>
      {kind === 'dish' && <label>Does this include cooking oil/salt?
        <select className="restyle-control" value={additions} onChange={(e) => setAdditions(e.target.value as typeof additions)}>
          <option value="unanswered">Not sure</option><option value="yes">Yes</option><option value="no">No</option>
        </select>
      </label>}
      <fieldset className="restyle-fieldset"><legend>Values per 100 {basis}</legend>
        {nutrients.map(([n, label]) => <div key={n} className="restyle-nutrient">
          <label className="restyle-check">
            <input type="checkbox" aria-label={`${label} known`} checked={known[n] ?? false}
              onChange={(e) => setKnown({ ...known, [n]: e.target.checked })} />{label} known
          </label>
          {known[n] ? <input className="restyle-control" aria-label={`${label} per 100 ${basis}`} inputMode="decimal"
            value={values[n] ?? ''} onChange={(e) => setValues({ ...values, [n]: e.target.value })} /> : <span>Unknown (?)</span>}
        </div>)}
      </fieldset>
      {mutation.error && <p role="alert">{mutation.error.message}</p>}
      <Button type="submit" disabled={mutation.isPending}>Create product</Button>
    </form>
    <OffImport />
  </main>
}
