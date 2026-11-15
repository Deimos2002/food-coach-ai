import type { ReactNode } from 'react'
import './PageHeader.css'

type PageHeaderProps = { title: string; actions?: ReactNode }

export function PageHeader({ title, actions }: PageHeaderProps) {
  return <header className="ui-page-header"><h1>{title}</h1>{actions && <div className="ui-page-header-actions">{actions}</div>}</header>
}
