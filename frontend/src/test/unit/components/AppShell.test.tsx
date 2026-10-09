import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../../components/AppShell'
import * as AuthHookModule from '../../../auth/useAuth'

function renderShell(role: string) {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r', user: { id: '1', email: 'a@b.com', name: 'An', is_admin: role === 'admin' },
    login: vi.fn(), logout: vi.fn(),
  })
  return render(
    <MemoryRouter>
      <AppShell title="Trang">content</AppShell>
    </MemoryRouter>,
  )
}

describe('AppShell', () => {
  it('gives a lecturer no error-tracking menu: that moved to the admin page', () => {
    renderShell('teacher')

    expect(screen.queryByText('Theo dõi lỗi')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /hello lms/i })).toHaveAttribute('href', '/courses')
  })

  it('takes an admin home to the admin page, not to courses they do not have', () => {
    renderShell('admin')

    expect(screen.getByRole('link', { name: /hello lms/i })).toHaveAttribute('href', '/admin')
    expect(screen.queryByText('Theo dõi lỗi')).not.toBeInTheDocument()
  })
})
