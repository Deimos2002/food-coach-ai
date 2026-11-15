import { Button, PageHeader } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { acceptRevision, createOverride, getProduct, reconfirmOverride, setCookingAdditions } from '../../lib/api'
import { formatRange } from '../../lib/format'
import type { Nutrient, OverrideIn, Product, ProductValue } from '../../lib/types'

const label = (name: string) => name.replaceAll('_', ' ')
const nutrients: Nutrient[] = ['energy_kcal', 'protein', 'carbohydrate', 'sugars', 'fat',
  'saturated_fat', 'fibre', 'salt', 'caffeine', 'alcohol', 'polyols', 'erythritol']

function valueText(nutrient: string, value: ProductValue | undefined) {
  return formatRange(value?.known ? value.lower : null, value?.known ? value.upper : null,
    nutrient === 'energy_kcal' ? 'kcal' : nutrient === 'caffeine' ? 'mg' : 'g')
}

function ProductDetail({ product }: { product: Product }) {
  const cache = useQueryClient()
  const [nutrient, setNutrient] = useState<Nutrient>('salt')
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [retain, setRetain] = useState(false)
  const [additions, setAdditions] = useState<'yes' | 'no' | 'unanswered'>(product.includes_cooking_additions ?? 'unanswered')
  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ['product', product.product_id] }),
      cache.invalidateQueries({ queryKey: ['day'] }),
      cache.invalidateQueries({ queryKey: ['search'] }),
    ])
  }
  const accept = useMutation({ mutationFn: (id: number) => acceptRevision(product.product_id, id), onSuccess: refresh })
  const reconfirm = useMutation({ mutationFn: reconfirmOverride, onSuccess: refresh })
  const override = useMutation({ mutationFn: (body: OverrideIn) => createOverride(product.product_id, body), onSuccess: refresh })
  const cooking = useMutation({ mutationFn: () => setCookingAdditions(product.product_id, additions), onSuccess: refresh })
  const pending = accept.isPending || reconfirm.isPending || override.isPending || cooking.isPending
  return <main className="restyle-page ui-card">
    <PageHeader title={product.name} />
    {product.brand && <p>{product.brand}</p>}
    <p>{product.owner === 'user' ? 'Your product' : 'Catalogue product'} · {product.kind}</p>
    <h2>Current values per 100 {product.basis_unit}</h2>
    <dl>{nutrients.map((n) => <div key={n} className="restyle-value">
      <dt>{label(n)}</dt><dd className="restyle-description">{valueText(n, product.values[n])} · {product.values[n]?.source ?? 'Unknown source'} · {product.values[n]?.verification ?? 'Unknown verification'}
        {product.values[n]?.qualifier === 'trace' && ' · trace (unquantified)'}{product.values[n]?.qualifier === 'less_than' && ' · less than'}
      </dd>
    </div>)}</dl>
    {product.owner === 'user' && product.kind === 'dish' && <form className="restyle-form" onSubmit={(e) => { e.preventDefault(); cooking.mutate() }}>
      <label>Does this include cooking oil/salt?
        <select className="restyle-control" value={additions} onChange={(e) => setAdditions(e.target.value as typeof additions)}>
          <option value="unanswered">Not sure</option><option value="yes">Yes</option><option value="no">No</option>
        </select>
      </label><Button type="submit" disabled={pending}>Save cooking additions</Button>
    </form>}
    <h2>Revisions</h2>
    {product.revisions.length === 0 && <p>No revisions.</p>}
    <ul>{product.revisions.map((r) => <li key={r.id}>
      Revision {r.id} · {label(r.status)} · {r.created_at}
      {r.status === 'pending_review' && product.owner === 'user' &&
        <Button disabled={pending} onClick={() => accept.mutate(r.id)}>Accept</Button>}
    </li>)}</ul>
    <h2>Overrides</h2>
    {product.overrides.length === 0 && <p>No overrides.</p>}
    <ul>{product.overrides.map((o) => <li key={o.id}>
      {label(o.nutrient)} · {valueText(o.nutrient, o.value)} · version {o.version} · {o.value.source ?? 'Unknown source'} · {o.value.verification ?? 'Unknown verification'}
      {o.needs_reconfirm && <><span> · Needs reconfirmation </span>
        <Button disabled={pending} onClick={() => reconfirm.mutate(o.id)}>Reconfirm</Button></>}
    </li>)}</ul>
    <form className="restyle-form" onSubmit={(e) => {
      e.preventDefault()
      override.mutate({ nutrient, value: value.trim() ? { value: value.trim().replace(',', '.') } : null,
        reason, retain_across_revisions: retain })
    }}>
      <h2>Add correction</h2>
      <label>Nutrient <select className="restyle-control" value={nutrient} onChange={(e) => setNutrient(e.target.value as Nutrient)}>
        {nutrients.map((n) => <option key={n} value={n}>{label(n)}</option>)}
      </select></label>
      <label>Corrected value per 100 {product.basis_unit} <input className="restyle-control" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} /></label>
      <p>Leave empty to mark unknown.</p>
      <label>Reason <input className="restyle-control" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <label className="restyle-check"><input type="checkbox" checked={retain} onChange={(e) => setRetain(e.target.checked)} />Retain across revisions</label>
      <Button type="submit" disabled={pending}>Save correction</Button>
    </form>
    {[accept.error, reconfirm.error, override.error, cooking.error].filter(Boolean).map((e, i) => <p role="alert" key={i}>{e?.message}</p>)}
    <Link className="restyle-link" to="/products/new">Create another product</Link>
  </main>
}

export function ProductPage() {
  const { id = '' } = useParams()
  // IDs are integers, not decimal nutrient or quantity values.
  const productId = parseInt(id, 10)
  const validId = /^\d+$/.test(id) && productId > 0
  const query = useQuery({ queryKey: ['product', productId], queryFn: () => getProduct(productId), enabled: validId })
  if (!validId) return <p role="alert">Invalid product ID.</p>
  if (query.isPending) return <p role="status">Loading product…</p>
  if (query.error) return <p role="alert">{query.error.message}</p>
  return <ProductDetail key={query.data.product_id} product={query.data} />
}
