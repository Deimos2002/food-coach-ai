import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ThemeToggle } from './ThemeToggle'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('starts with system appearance, switches modes and remembers the choice', () => {
  const { unmount } = render(<ThemeToggle />)
  const toggle = screen.getByRole('button', { name: 'Dark mode' })
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
  expect(document.documentElement.dataset.theme).toBe('dark')
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('foodcoach-theme')).toBe('light')
  unmount()
  render(<ThemeToggle />)
  expect(screen.getByRole('button', { name: 'Dark mode' })).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }))
  expect(document.documentElement.dataset.theme).toBe('dark')
})

it('ignores invalid stored themes', () => {
  localStorage.setItem('foodcoach-theme', 'orange')
  render(<ThemeToggle />)
  expect(document.documentElement.dataset.theme).toBe('dark')
})

it('still switches when browser storage is unavailable', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('blocked') })
  render(<ThemeToggle />)
  fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }))
  expect(document.documentElement.dataset.theme).toBe('light')
})

it('matches the mobile browser color to the selected appearance', () => {
  const meta = document.createElement('meta')
  meta.name = 'theme-color'
  document.head.append(meta)
  render(<ThemeToggle />)
  expect(meta.content).toBe('#111a16')
  fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }))
  expect(meta.content).toBe('#f2f5f3')
  meta.remove()
})
