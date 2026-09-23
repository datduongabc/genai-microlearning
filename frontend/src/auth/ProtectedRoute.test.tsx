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
        <Route element={<ProtectedRoute />}>
          <Route path="/private" element={<div>Private content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
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
})
