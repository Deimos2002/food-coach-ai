import { screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { expect, it } from 'vitest'
import { renderWithProviders } from '../../test/render'
import { localDay } from '../../lib/dates'
import { DateSwitcher } from '../../ui/DateSwitcher'

it('date switcher moves a day and back to today', () => {
  function Example() {
    const [day, setDay] = useState('2026-10-13')
    return <DateSwitcher day={day} onChange={setDay} />
  }
  renderWithProviders(<Example />)
  fireEvent.click(screen.getByRole('button', { name: 'Previous day' }))
  expect(screen.getByRole('button', { name: /2026-10-12/ })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Next day' }))
  expect(screen.getByRole('button', { name: /2026-10-13/ })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Today' }))
  expect(screen.getByRole('button', { name: new RegExp(localDay()) })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: new RegExp(localDay()) }))
  fireEvent.change(screen.getByLabelText('Choose date'), { target: { value: '2026-10-09' } })
  expect(screen.getByRole('button', { name: /2026-10-09/ })).toBeInTheDocument()
})

