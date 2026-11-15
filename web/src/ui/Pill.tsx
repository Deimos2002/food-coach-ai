import type { MouseEventHandler, ReactNode } from 'react'
import './Pill.css'

type PillProps = { selected?: boolean; onClick?: MouseEventHandler<HTMLButtonElement>; children: ReactNode }

export function Pill({ selected = false, onClick, children }: PillProps) {
  const className = `ui-pill${selected ? ' ui-pill--selected' : ''}`
  return onClick ? <button type="button" className={className} aria-pressed={selected} onClick={onClick}>{children}</button> : <span className={className}>{children}</span>
}
