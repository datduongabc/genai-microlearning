import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { lazy, Suspense, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfirmHost } from '../../../components/ConfirmHost'
import { ErrorBoundary } from '../../../components/ErrorBoundary'
import { Tabs } from '../../../components/Tabs'
import { confirmAction } from '../../../lib/confirm'

describe('ErrorBoundary', () => {
  // React reports every caught render error on the console; the tests expect them.
  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { vi.restoreAllMocks() })

  it('asks for a reload when a page chunk from an older deploy can no longer be loaded', async () => {
    const reload = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload })
    const Gone = lazy(() => Promise.reject(new TypeError('Failed to fetch dynamically imported module: /assets/ModulePreviewPage-old.js')))
    render(<ErrorBoundary><Suspense fallback={null}><Gone /></Suspense></ErrorBoundary>)
    expect(await screen.findByRole('alert')).toHaveTextContent('Đã có phiên bản mới, tải lại trang')
    await userEvent.click(screen.getByRole('button', { name: 'Tải lại trang' }))
    expect(reload).toHaveBeenCalledOnce()
  })

  it('shows a general message for any other render error instead of a blank page', () => {
    function Broken(): never { throw new Error('boom') }
    render(<ErrorBoundary><Broken /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toHaveTextContent('Đã có lỗi khi hiển thị trang')
    expect(screen.getByRole('alert')).not.toHaveTextContent('phiên bản mới')
  })
})

function TabsHarness() {
  const [tab, setTab] = useState('a')
  return <Tabs tabs={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]} selected={tab} onSelect={setTab} label="t">
    <p>panel {tab}</p>
  </Tabs>
}

describe('Tabs', () => {
  it('marks a tab focused by a mouse press, so its ring stays hidden, and forgets it on blur', async () => {
    render(<TabsHarness />)
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

function askConfirm() {
  let answer!: Promise<boolean>
  act(() => { answer = confirmAction({ title: 'Xóa tài liệu "a.pdf"?', description: 'Không thể khôi phục.', action: 'Xóa tài liệu', destructive: true }) })
  return answer
}

describe('ConfirmHost', () => {
  it('shows the question and answers yes from the action button', async () => {
    render(<ConfirmHost />)
    const answer = askConfirm()
    const dialog = await screen.findByRole('alertdialog', { name: 'Xóa tài liệu "a.pdf"?' })
    expect(dialog).toHaveTextContent('Không thể khôi phục.')
    await userEvent.click(screen.getByRole('button', { name: 'Xóa tài liệu' }))
    await expect(answer).resolves.toBe(true)
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('answers no from Cancel and gives focus back to what asked', async () => {
    render(<><button>Xóa</button><ConfirmHost /></>)
    screen.getByRole('button', { name: 'Xóa' }).focus()
    const answer = askConfirm()
    await userEvent.click(await screen.findByRole('button', { name: 'Hủy' }))
    await expect(answer).resolves.toBe(false)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Xóa' })).toHaveFocus())
  })

  it('answers no from Escape, with focus starting on Cancel so Enter cannot delete by accident', async () => {
    render(<ConfirmHost />)
    const answer = askConfirm()
    await screen.findByRole('alertdialog')
    expect(screen.getByRole('button', { name: 'Hủy' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    await expect(answer).resolves.toBe(false)
  })
})
