import { Button, PageHeader } from '../../ui'
import '../restyle.css'
import '../../ui/Card.css'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { createAlias, listAliases, updateAlias } from '../../lib/api'
import { ProductSearch, type PickedProduct } from '../add/ProductSearch'
import type { Alias } from '../../lib/types'

interface AliasFormProps {
  initial?: Alias
  onClose: () => void
}

/** A new or edited alias picks its product through search. */
function AliasForm({ initial, onClose }: AliasFormProps) {
  const qc = useQueryClient()
  const [text, setText] = useState(initial?.text ?? '')
  const [product, setProduct] = useState<PickedProduct | null>(null)
  const productId = product?.product_id ?? initial?.product_id ?? null

  const mutation = useMutation({
    mutationFn: () => {
      if (productId === null) throw new Error('pick a product')
      return initial ? updateAlias(initial.id, text, productId) : createAlias(text, productId)
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['aliases'] })
      onClose()
    },
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    mutation.mutate()
  }

  return (
    <div className="restyle-dialog ui-card" role="dialog" aria-modal="true" aria-label={initial ? 'Edit alias' : 'New alias'}>
      <form className="restyle-form" onSubmit={submit}>
        <label htmlFor="alias-text">Alias text</label>
        <input id="alias-text" value={text} onChange={(e) => setText(e.target.value)} />
        {product ? (
          <p>
            <strong>{product.name}</strong>{' '}
            <Button type="button" onClick={() => setProduct(null)}>
              Change
            </Button>
          </p>
        ) : (
          <ProductSearch onPick={setProduct} />
        )}
        {mutation.error && <p role="alert">{mutation.error.message}</p>}
        <Button type="submit" disabled={mutation.isPending || !text.trim() || productId === null}>
          Save alias
        </Button>
        <Button variant="secondary" type="button" onClick={onClose}>
          Cancel
        </Button>
      </form>
    </div>
  )
}

export function AliasesPage() {
  const list = useQuery({ queryKey: ['aliases'], queryFn: () => listAliases() })
  const [editing, setEditing] = useState<Alias | 'new' | null>(null)

  return (
    <main className="restyle-page ui-card">
      <PageHeader title="Aliases" />
      {list.error && <p role="alert">{list.error.message}</p>}
      <ul>
        {(list.data ?? []).map((a) => (
          <li key={a.id}>
            {a.text}
            {a.suspended && <span> Suspended</span>}
            <Button type="button" onClick={() => setEditing(a)}>
              Edit
            </Button>
          </li>
        ))}
      </ul>
      <Button type="button" onClick={() => setEditing('new')}>
        New alias
      </Button>
      {editing === 'new' && <AliasForm onClose={() => setEditing(null)} />}
      {editing !== null && editing !== 'new' && <AliasForm initial={editing} onClose={() => setEditing(null)} />}
    </main>
  )
}
