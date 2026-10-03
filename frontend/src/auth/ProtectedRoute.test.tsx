import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from './ProtectedRoute'
import * as AuthHookModule from './useAuth'

function renderProtected(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/login" element={<div>Login page</div>} />
        <Route path="/courses" element={<div>Courses page</div>} />
        <Route path="/admin" element={<div>Admin page</div>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/private" element={<div>Private content</div>} />
        </Route>
        <Route element={<ProtectedRoute role="admin" />}>
          <Route path="/admin-only" element={<div>Admin only content</div>} />
        </Route>
        <Route element={<ProtectedRoute role="teacher" />}>
          <Route path="/teacher-only" element={<div>Teacher only content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function signedInAs(role: string) {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 'a-token',
    refreshToken: 'a-refresh-token',
    user: { id: '1', email: 'a@b.com', name: 'A', role },
    login: vi.fn(),
    logout: vi.fn(),
  })
}

describe('ProtectedRoute', () => {
  it('redirects to /login when there is no token', () => {
    vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
      token: null,
      refreshToken: null,
      user: null,
      login: vi.fn(),
      logout: vi.fn(),
    })

    renderProtected('/private')

    expect(screen.getByText('Login page')).toBeInTheDocument()
    expect(screen.queryByText('Private content')).not.toBeInTheDocument()
  })

  it('renders the protected content when a token is present', () => {
    vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
      token: 'a-token',
      refreshToken: 'a-refresh-token',
      user: { id: '1', email: 'a@b.com', name: 'A', role: 'teacher' },
      login: vi.fn(),
      logout: vi.fn(),
    })

    renderProtected('/private')

    expect(screen.getByText('Private content')).toBeInTheDocument()
  })

  it('keeps a lecturer out of the admin pages and an admin out of the lecturer pages', () => {
    signedInAs('teacher')
    const { unmount } = renderProtected('/admin-only')
    expect(screen.getByText('Courses page')).toBeInTheDocument()
    expect(screen.queryByText('Admin only content')).not.toBeInTheDocument()
    unmount()

    signedInAs('admin')
    renderProtected('/teacher-only')
    expect(screen.getByText('Admin page')).toBeInTheDocument()
    expect(screen.queryByText('Teacher only content')).not.toBeInTheDocument()
  })

  it('lets each role into its own pages', () => {
    signedInAs('admin')
    const { unmount } = renderProtected('/admin-only')
    expect(screen.getByText('Admin only content')).toBeInTheDocument()
    unmount()

    signedInAs('teacher')
    renderProtected('/teacher-only')
    expect(screen.getByText('Teacher only content')).toBeInTheDocument()
  })
})
