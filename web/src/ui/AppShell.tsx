import type { ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import './AppShell.css'
import { ThemeToggle } from './ThemeToggle'

const icons = {
  Today: 'M4 5h16v15H4z M8 3v4 M16 3v4 M4 10h16 M8 14h3 M8 17h7',
  Progress: 'M4 19h16 M6 15V9 M12 15V4 M18 15v-7',
  Coach: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  Me: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 21v-2a8 8 0 0 1 16 0v2',
}

function NavIcon({ name }: { name: keyof typeof icons }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><path d={icons[name]} /></svg>
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation()
  const todayActive = pathname === '/' || pathname.startsWith('/day/') || pathname.startsWith('/add/')
  return (
    <div className="app-shell">
      <nav className="app-shell-nav" aria-label="Primary">
        <div className="app-brand" aria-label="Foodcoach">
          <span className="app-brand-mark" aria-hidden="true" />
          <span className="app-brand-name">Foodcoach</span>
          <span className="app-brand-tagline">Eat well, feel good</span>
        </div>
        <Link to="/" aria-current={todayActive ? 'page' : undefined}><NavIcon name="Today" />Today</Link>
        <NavLink to="/progress"><NavIcon name="Progress" />Progress</NavLink>
        <NavLink to="/coach"><NavIcon name="Coach" />Coach</NavLink>
        <NavLink to="/me"><NavIcon name="Me" />Me</NavLink>
        <p className="app-nav-note">Your daily<br />food journal.</p>
      </nav>
      <div className="app-content"><div className="app-appearance"><ThemeToggle /></div>{children}</div>
    </div>
  )
}
