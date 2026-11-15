import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { Ring } from './Ring'

afterEach(cleanup)

it('ring shows ? for unknown', () => {
  render(<Ring value={null} target={2100} label="Energy" unit="kcal" />)
  expect(screen.getByText('?')).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Energy ? of 2100 kcal' })).toBeInTheDocument()
})

it('ring caps at 100% and shows real value', () => {
  render(<Ring value={2500} target={2000} label="Energy" unit="kcal" />)
  expect(screen.getByText('2500')).toBeInTheDocument()
  expect(screen.getByRole('img')).toHaveStyle({ background: 'conic-gradient(var(--accent) 100%, var(--border) 0)' })
})

it('macro bar exposes text value', async () => {
  const { MacroBar } = await import('./MacroBar')
  const { rerender } = render(<MacroBar label="Protein" value={72} target={120} unit="g" tone="protein" />)
  expect(screen.getByText('72 / 120 g')).toBeInTheDocument()
  rerender(<MacroBar label="Protein" value={null} target={null} unit="g" tone="protein" />)
  expect(screen.getByText('? / ? g')).toBeInTheDocument()
})

it('button variants render class and stay native buttons', async () => {
  const { Button } = await import('./Button')
  for (const variant of ['primary', 'secondary', 'ghost', 'danger'] as const) {
    const { unmount } = render(<Button variant={variant} disabled name="save">Save</Button>)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button.tagName).toBe('BUTTON')
    expect(button).toHaveClass(`ui-button--${variant}`)
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('name', 'save')
    expect(button).toHaveAttribute('type', 'button')
    unmount()
  }
})

it('pill is pressed when selected and static without an action', async () => {
  const { Pill } = await import('./Pill')
  const { rerender } = render(<Pill selected onClick={() => {}}>Portion</Pill>)
  expect(screen.getByRole('button', { name: 'Portion' })).toHaveAttribute('aria-pressed', 'true')
  rerender(<Pill>Portion</Pill>)
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.getByText('Portion')).not.toHaveAttribute('aria-pressed')
})

it('tabs switch with keyboard arrows', async () => {
  const { Tabs } = await import('./Tabs')
  const { useState } = await import('react')
  const { default: userEvent } = await import('@testing-library/user-event')
  function Example() {
    const [value, onChange] = useState('recent')
    return <Tabs items={[{ id: 'recent', label: 'Recent' }, { id: 'saved', label: 'Saved' }]} value={value} onChange={onChange} />
  }
  render(<Example />)
  const user = userEvent.setup()
  expect(screen.getByRole('tablist')).toBeInTheDocument()
  await user.tab()
  await user.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab', { name: 'Saved' })).toHaveFocus()
  expect(screen.getByRole('tab', { name: 'Saved' })).toHaveAttribute('aria-selected', 'true')
  await user.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab', { name: 'Recent' })).toHaveFocus()
  await user.keyboard('{ArrowLeft}')
  expect(screen.getByRole('tab', { name: 'Saved' })).toHaveFocus()
  await user.click(screen.getByRole('tab', { name: 'Recent' }))
  expect(screen.getByRole('tab', { name: 'Recent' })).toHaveAttribute('aria-selected', 'true')
})

it('card renders its content using the requested element and class', async () => {
  const { Card } = await import('./Card')
  render(<Card as="article" className="meal">Lunch</Card>)
  expect(screen.getByRole('article')).toHaveTextContent('Lunch')
  expect(screen.getByRole('article')).toHaveClass('meal')
})

it('badge labels its tone with visible content', async () => {
  const { Badge } = await import('./Badge')
  render(<><Badge tone="accent">Estimate</Badge><Badge tone="muted">Dish</Badge></>)
  expect(screen.getByText('Estimate')).toHaveClass('ui-badge--accent')
  expect(screen.getByText('Dish')).toHaveClass('ui-badge--muted')
})

it('page header exposes a title and its actions', async () => {
  const { PageHeader } = await import('./PageHeader')
  render(<PageHeader title="Today" actions={<button>Next day</button>} />)
  expect(screen.getByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Next day' })).toBeInTheDocument()
})
