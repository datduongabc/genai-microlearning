import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../../auth/AuthContext'
import { useAuth } from '../../../auth/useAuth'
import * as api from '../../../api'

vi.mock('../../../api')

function Probe() {
  const { token, user, login, logout } = useAuth()
  return (
    <div>
      <span data-testid="token">{token ?? 'no-token'}</span>
      <span data-testid="user">{user?.email ?? 'no-user'}</span>
      <button onClick={() => login('a@b.com', 'password123')}>login</button>
      <button onClick={() => logout()}>logout</button>
    </div>
  )
}

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})

describe('AuthProvider', () => {
  it('starts with no auth when localStorage is empty', () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )
    expect(screen.getByTestId('token')).toHaveTextContent('no-token')
  })

  it('login stores the returned token/user and persists to localStorage', async () => {
    vi.mocked(api.login).mockResolvedValue({
      access_token: 'tok-123',
      refresh_token: 'refresh-123',
      token_type: 'bearer',
      user: { id: '1', email: 'a@b.com', name: 'A', is_admin: false },
    })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    // userEvent is already wrapped in act by Testing Library: wrapping it again
    // makes React print "not configured to support act(...)".
    await userEvent.click(screen.getByText('login'))

    await waitFor(() => expect(screen.getByTestId('token')).toHaveTextContent('tok-123'))
    expect(screen.getByTestId('user')).toHaveTextContent('a@b.com')
    await waitFor(() => expect(JSON.parse(localStorage.getItem('auth')!).token).toBe('tok-123'))
  })

  it('logout clears state and localStorage, and revokes the refresh token server-side', async () => {
    vi.mocked(api.login).mockResolvedValue({
      access_token: 'tok-123',
      refresh_token: 'refresh-123',
      token_type: 'bearer',
      user: { id: '1', email: 'a@b.com', name: 'A', is_admin: false },
    })
    vi.mocked(api.logout).mockResolvedValue(undefined)

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))
    await waitFor(() => expect(screen.getByTestId('token')).toHaveTextContent('tok-123'))
    await userEvent.click(screen.getByText('logout'))

    await waitFor(() => expect(screen.getByTestId('token')).toHaveTextContent('no-token'))
    await waitFor(() => expect(localStorage.getItem('auth')).toBeNull())
    expect(api.logout).toHaveBeenCalledWith('refresh-123')
  })

  it('logout revokes the refresh token the background refresh stored, not the spent login one', async () => {
    vi.mocked(api.login).mockResolvedValue({
      access_token: 'tok-123',
      refresh_token: 'refresh-123',
      token_type: 'bearer',
      user: { id: '1', email: 'a@b.com', name: 'A', is_admin: false },
    })
    vi.mocked(api.logout).mockResolvedValue(undefined)

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await userEvent.click(screen.getByText('login'))
    await waitFor(() => expect(JSON.parse(localStorage.getItem('auth')!).refreshToken).toBe('refresh-123'))
    // What client.ts does when it renews the access token: the login one is revoked server-side.
    const stored = JSON.parse(localStorage.getItem('auth')!)
    localStorage.setItem('auth', JSON.stringify({ ...stored, token: 'tok-456', refreshToken: 'refresh-456' }))
    await userEvent.click(screen.getByText('logout'))

    expect(api.logout).toHaveBeenCalledWith('refresh-456')
  })
})
