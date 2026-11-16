import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { lazy, Suspense } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ErrorBoundary } from '../../../components/ErrorBoundary'

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
