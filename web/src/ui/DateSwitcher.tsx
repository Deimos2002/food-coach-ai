import { useState } from 'react'
import { addDays, localDay } from '../lib/dates'
import { Button } from './Button'
import './DateSwitcher.css'
export function DateSwitcher({ day, onChange }: { day: string; onChange: (day: string) => void }) {
  const [picking, setPicking] = useState(false)
  const label = new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
  return <nav className="date-switcher" aria-label="Day">
    <Button variant="ghost" aria-label="Previous day" onClick={() => onChange(addDays(day, -1))}>‹</Button>
    <Button variant="secondary" aria-label={`Choose date ${day}`} onClick={() => setPicking(!picking)}>{label}</Button>
    <Button variant="ghost" aria-label="Next day" onClick={() => onChange(addDays(day, 1))}>›</Button>
    <Button variant="ghost" onClick={() => onChange(localDay())}>Today</Button>
    {picking && <input aria-label="Choose date" type="date" value={day} onChange={(e) => {
      if (e.target.value) { onChange(e.target.value); setPicking(false) }
    }} />}
  </nav>
}
