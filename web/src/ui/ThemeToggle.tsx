import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem('foodcoach-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* Browser storage can be disabled. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111a16' : '#f2f5f3')
    try { localStorage.setItem('foodcoach-theme', theme) } catch { /* Switching still works without persistence. */ }
  }, [theme])

  return <button className="theme-toggle" type="button" aria-label="Dark mode" aria-pressed={theme === 'dark'} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
    <svg aria-hidden="true" viewBox="0 0 24 24">
      {theme === 'dark' ? <path d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z" /> : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></>}
    </svg>
    {theme === 'dark' ? 'Dark' : 'Light'}
  </button>
}
