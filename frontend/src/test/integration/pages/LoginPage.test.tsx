import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from '../../../pages/LoginPage'
import * as api from '../../../api'
import * as AuthHookModule from '../../../auth/useAuth'

const mockLogin = vi.fn()

beforeEach(() => {
  mockLogin.mockReset()
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: null,
    refreshToken: null,
    user: null,
    login: mockLogin,
    loginWithLaunchCode: vi.fn(),
    logout: vi.fn(),
  })
})

function refusal(status: number, data: unknown) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data, headers: {}, config } as AxiosResponse
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, null, response)
}

async function fillAndSubmit(password = 'password123') {
  await userEvent.type(screen.getByPlaceholderText('ten@truong.edu.vn'), 'teacher@example.com')
  await userEvent.type(screen.getByLabelText('Mật khẩu'), password)
  await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))
}

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  it('tells a user who just signed up that the account was created, and not otherwise', () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { justRegistered: true } }]}>
        <LoginPage />
      </MemoryRouter>,
    )
    expect(screen.getByText('Đã tạo tài khoản. Hãy đăng nhập.')).toBeInTheDocument()
    unmount()

    renderLoginPage()
    expect(screen.queryByText('Đã tạo tài khoản. Hãy đăng nhập.')).not.toBeInTheDocument()
  })

  it('shows validation errors instead of calling login when the form is invalid', async () => {
    renderLoginPage()

    await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))

    expect(await screen.findByText(/email không hợp lệ/i)).toBeInTheDocument()
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('calls login with the entered credentials when the form is valid', async () => {
    mockLogin.mockResolvedValue({ id: '1', email: 'teacher@example.com', name: 'User', is_admin: false })
    renderLoginPage()

    await userEvent.type(screen.getByPlaceholderText('ten@truong.edu.vn'), 'teacher@example.com')
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('teacher@example.com', 'password123')
    })
  })

  it('shows the server error message when login fails', async () => {
    mockLogin.mockRejectedValue(new Error('network down'))
    renderLoginPage()

    await userEvent.type(screen.getByPlaceholderText('ten@truong.edu.vn'), 'teacher@example.com')
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))

    expect(await screen.findByText(/đăng nhập thất bại/i)).toBeInTheDocument()
  })

  it('shows what the server said when it refuses the login', async () => {
    mockLogin.mockRejectedValue(refusal(401, { detail: 'Invalid email or password' }))
    renderLoginPage()

    await fillAndSubmit()

    expect(await screen.findByText('Invalid email or password')).toBeInTheDocument()
  })

  it('tells the person to slow down when the server limits their attempts', async () => {
    mockLogin.mockRejectedValue(refusal(429, { error: 'Rate limit exceeded: 10 per 1 minute' }))
    renderLoginPage()

    await fillAndSubmit()

    expect(await screen.findByText(/quá nhanh/i)).toBeInTheDocument()
  })

  it('lets a short password through to the server, which alone decides whether it is right', async () => {
    mockLogin.mockResolvedValue({ id: '1', email: 'teacher@example.com', name: 'User', is_admin: false })
    renderLoginPage()

    await fillAndSubmit('old-pw')

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('teacher@example.com', 'old-pw'))
  })

  it('tells the password manager which field is the username and which the current password', () => {
    renderLoginPage()

    expect(screen.getByPlaceholderText('ten@truong.edu.vn')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Mật khẩu')).toHaveAttribute('autocomplete', 'current-password')
  })
})

it('offers open registration and admin support', async () => {
  vi.spyOn(api, 'getAuthOptions').mockResolvedValue({ self_registration: true })
  renderLoginPage()
  expect(await screen.findByRole('link', { name: 'Đăng ký' })).toHaveAttribute('href', '/register')
  expect(screen.getByRole('link', { name: 'Quên mật khẩu?' })).toHaveAttribute('href', expect.stringContaining('mailto:'))
  expect(screen.getByRole('link', { name: 'Quên mật khẩu?' })).toHaveAccessibleDescription('Liên hệ quản trị viên để đặt lại mật khẩu.')
})
