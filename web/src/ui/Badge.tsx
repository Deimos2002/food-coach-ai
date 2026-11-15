import type { ReactNode } from 'react'
import './Badge.css'

type BadgeProps = { tone: 'accent' | 'muted'; children: ReactNode }

export function Badge({ tone, children }: BadgeProps) {
  return <span className={`ui-badge ui-badge--${tone}`}>{children}</span>
}
