import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Tabs } from '../../../components/Tabs'

function Harness() {
  const [tab, setTab] = useState('a')
  return <Tabs tabs={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]} selected={tab} onSelect={setTab} label="t">
    <p>panel {tab}</p>
  </Tabs>
}

describe('Tabs', () => {
  it('marks a tab focused by a mouse press, so its ring stays hidden, and forgets it on blur', async () => {
    render(<Harness />)
    const b = screen.getByRole('tab', { name: 'B' })
    fireEvent.mouseDown(b, { button: 0 })
    expect(b).toHaveAttribute('data-pointer')
    expect(screen.getByText('panel b')).toBeInTheDocument()

    await userEvent.keyboard('{ArrowLeft}')
    const a = screen.getByRole('tab', { name: 'A' })
    expect(a).toHaveFocus()
    expect(a).not.toHaveAttribute('data-pointer')
    expect(b).not.toHaveAttribute('data-pointer')
  })
})
