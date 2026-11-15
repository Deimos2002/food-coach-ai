import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { deleteAllData, exportData } from '../../lib/api'
import { localDay } from '../../lib/dates'
import { TrialStats } from './TrialStats'
import { CoachOps } from '../coach/CoachOps'
import { Button } from '../../ui/Button'
import '../me/MePage.css'

function DeleteDialog({ onClose }: { onClose: () => void }) {
  const cache = useQueryClient()
  const [text, setText] = useState('')
  const [done, setDone] = useState(false)
  const mutation = useMutation({
    mutationFn: deleteAllData,
    onSuccess: async () => {
      await cache.resetQueries()
      setDone(true)
    },
  })
  if (done) return <p role="status">All your data was deleted</p>
  return (
    <div className="your-data-dialog" role="dialog" aria-label="Delete all my data" aria-modal="true">
      <p>This permanently deletes everything you have logged. This cannot be undone.</p>
      <label>
        Type DELETE to confirm
        <input
          value={text}
          disabled={mutation.isPending}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <Button
        variant="danger"
        disabled={text !== 'DELETE' || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        Delete everything
      </Button>
      <Button variant="secondary" disabled={mutation.isPending} onClick={onClose}>
        Cancel
      </Button>
      {mutation.error && <p role="alert">{mutation.error.message}</p>}
    </div>
  )
}

function download(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function YourData() {
  const [deleting, setDeleting] = useState(false)
  const exportMutation = useMutation({
    mutationFn: exportData,
    onSuccess: (result) => download(`foodcoach-export-${localDay()}.json`, result),
  })

  return (
    <section className="your-data" aria-label="Your data">
      <h2>Your data</h2>
      <Button variant="secondary" disabled={exportMutation.isPending} onClick={() => exportMutation.mutate()}>
        Export my data
      </Button>
      {exportMutation.error && <p role="alert">{exportMutation.error.message}</p>}

      {!deleting && (
        <Button variant="danger" onClick={() => setDeleting(true)}>
          Delete all my data
        </Button>
      )}
      {deleting && <DeleteDialog onClose={() => setDeleting(false)} />}
    </section>
  )
}

export function SettingsPage() {
  return (
    <main className="me-page">
      <h1>Settings</h1>
      <nav aria-label="Other settings">
        <Link className="me-link" to="/profile">Profile</Link>
        <Link className="me-link" to="/aliases">Aliases</Link>
      </nav>
      <YourData />
      <TrialStats />
      <CoachOps />
    </main>
  )
}
