import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../../api'
import * as AuthHookModule from '../../../auth/useAuth'
import { LoginPage } from '../../../pages/LoginPage'
import { RegisterPage } from '../../../pages/RegisterPage'

// A public deployment closes self registration (backend settings.self_registration).
beforeEach(() => {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: null, refreshToken: null, user: null,
    login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

function renderAt(page: React.ReactElement) {
  return render(<MemoryRouter>{page}</MemoryRouter>)
}

describe('sign-in pages when registration is closed', () => {
  it('the login page drops the sign-up link and says how to get an account', async () => {
    vi.spyOn(api, 'getAuthOptions').mockResolvedValue({ self_registration: false })
    renderAt(<LoginPage />)

    expect(await screen.findByText(/Nhờ quản trị viên tạo tài khoản/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Đăng ký' })).not.toBeInTheDocument()
  })

  it('the login page keeps the sign-up link while registration is open', async () => {
    vi.spyOn(api, 'getAuthOptions').mockResolvedValue({ self_registration: true })
    renderAt(<LoginPage />)

    expect(await screen.findByRole('link', { name: 'Đăng ký' })).toBeInTheDocument()
  })

  it('the register page explains instead of showing a form the server would refuse', async () => {
    vi.spyOn(api, 'getAuthOptions').mockResolvedValue({ self_registration: false })
    const register = vi.spyOn(api, 'register')
    renderAt(<RegisterPage />)

    expect(await screen.findByText(/Đăng ký tài khoản đã đóng/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: /tạo tài khoản/i })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Đăng nhập' })).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })
})
