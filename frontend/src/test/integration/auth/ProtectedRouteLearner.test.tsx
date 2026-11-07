import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ProtectedRoute } from '../../../auth/ProtectedRoute'
import * as AuthHookModule from '../../../auth/useAuth'

function renderAs(canAuthor: boolean | undefined) {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r',
    user: { id: 'u', email: 'e', name: 'n', is_admin: false, can_author: canAuthor },
    login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
  })
  return render(
    <MemoryRouter initialEntries={['/courses']}>
      <Routes>
        <Route element={<ProtectedRoute authorOnly />}>
          <Route path="/courses" element={<p>course list</p>} />
        </Route>
        <Route path="/learn" element={<p>learner home</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProtectedRoute authorOnly', () => {
  it('sends a student account away from the author pages', () => {
    renderAs(false)
    expect(screen.getByText('learner home')).toBeInTheDocument()
  })

  it('lets an author in, including an account from before the flag existed', () => {
    renderAs(undefined)
    expect(screen.getByText('course list')).toBeInTheDocument()
  })
})
