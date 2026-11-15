import { Button, Card } from '../../ui'
import '../restyle.css'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, importOff, offSearch } from '../../lib/api'
import type { Unit } from '../../lib/types'
import { returnTarget } from './returnTarget'


export function OffImport() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const cache = useQueryClient()
  const [barcode, setBarcode] = useState('')
  const [query, setQuery] = useState('')
  const [needsBasis, setNeedsBasis] = useState<string | null>(null)
  const lookup = useMutation({ mutationFn: offSearch })
  const importer = useMutation({
    mutationFn: ({ code, basis }: { code: string; basis?: Unit }) => importOff(code, basis),
    onSuccess: async ({ product_id }) => {
      setNeedsBasis(null)
      await cache.invalidateQueries({ queryKey: ['search'] })
      const target = returnTarget(searchParams)
      if (target) {
        navigate(target.path, { state: { reopenMealId: target.mealId, productId: product_id } })
      } else {
        navigate(`/products/${product_id}`)
      }
    },
    onError: (error, variables) => {
      if (error instanceof ApiError && error.status === 409 &&
        typeof error.body === 'object' && error.body !== null &&
        'needs_basis' in error.body && error.body.needs_basis === true) {
        setNeedsBasis(variables.code)
      }
    },
  })
  function start(code: string, basis?: Unit) {
    if (importer.isPending) return
    setNeedsBasis(null)
    importer.mutate({ code, basis })
  }
  return <Card as="section" className="restyle-panel restyle-form">
    <h2>Import from Open Food Facts</h2>
    <form className="restyle-form" onSubmit={(event) => { event.preventDefault(); start(barcode.trim()) }}>
      <label>Barcode <input className="restyle-control" required disabled={importer.isPending} inputMode="numeric" value={barcode}
        onChange={(e) => { setBarcode(e.target.value); setNeedsBasis(null); importer.reset() }} /></label>
      <Button type="submit" disabled={importer.isPending}>Import barcode</Button>
    </form>
    <form className="restyle-form" onSubmit={(event) => { event.preventDefault(); lookup.mutate(query.trim()) }}>
      <label>OFF name search <input className="restyle-control" required value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      <Button type="submit" disabled={lookup.isPending}>Search Open Food Facts</Button>
    </form>
    {lookup.error && <p role="alert">{lookup.error.message}</p>}
    {lookup.isSuccess && lookup.data.length === 0 && <p>No products found.</p>}
    {lookup.data?.map((hit, index) => <Button className="restyle-wrap"
      key={`${hit.code ?? ''}-${index}`} disabled={importer.isPending || !hit.code}
      onClick={() => { if (hit.code) start(hit.code) }}>
      Import {hit.product_name || hit.code || 'Unnamed product'}
    </Button>)}
    {needsBasis && <fieldset className="restyle-fieldset"><legend>Per 100 g or per 100 ml?</legend>
      <Button type="button" disabled={importer.isPending} onClick={() => start(needsBasis, 'g')}>Per 100 g</Button>
      <Button type="button" disabled={importer.isPending} onClick={() => start(needsBasis, 'ml')}>Per 100 ml</Button>
    </fieldset>}
    {importer.error && !needsBasis && <p role="alert">{importer.error.message}</p>}
  </Card>
}
