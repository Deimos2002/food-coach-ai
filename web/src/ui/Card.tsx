import type { ElementType, ReactNode } from 'react'
import './Card.css'

type CardProps = { children: ReactNode; as?: ElementType; className?: string }

export function Card({ children, as: Component = 'div', className = '' }: CardProps) {
  return <Component className={`ui-card ${className}`}>{children}</Component>
}
