import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginPage } from './LoginPage'
import * as AuthHookModule from '../auth/useAuth'

const mockLogin = vi.fn()
const TEACHER = { id: '1', email: 'teacher@example.com', name: 'T', role: 'teacher' }

beforeEach(() => {
  mockLogin.mockReset()
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: null,
    refreshToken: null,
    user: null,
    login: mockLogin,
    logout: vi.fn(),
  })
})

function refusal(status: number, data: unknown) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data, headers: {}, config } as AxiosResponse
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, null, response)
}

async function fillAndSubmit(password = 'password123') {
  await userEvent.type(screen.getByPlaceholderText('ten@hcmut.edu.vn'), 'teacher@example.com')
  await userEvent.type(screen.getByPlaceholderText('••••••••'), password)
  await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))
}

function renderLoginPage() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/courses" element={<div>Trang giảng viên</div>} />
        <Route path="/admin" element={<div>Trang quản trị</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  it('shows validation errors instead of calling login when the form is invalid', async () => {
    renderLoginPage()

    await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))

    expect(await screen.findByText(/email không hợp lệ/i)).toBeInTheDocument()
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('calls login with the entered credentials when the form is valid', async () => {
    mockLogin.mockResolvedValue(TEACHER)
    renderLoginPage()

    await userEvent.type(screen.getByPlaceholderText('ten@hcmut.edu.vn'), 'teacher@example.com')
    await userEvent.type(screen.getByPlaceholderText('••••••••'), 'password123')
    await userEvent.click(screen.getByRole('button', { name: /đăng nhập/i }))

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith('teacher@example.com', 'password123')
    })
  })

  it('shows the server error message when login fails', async () => {
    mockLogin.mockRejectedValue(new Error('network down'))
    renderLoginPage()

    await userEvent.type(screen.getByPlaceholderText('ten@hcmut.edu.vn'), 'teacher@example.com')
    await userEvent.type(screen.getByPlaceholderText('••••••••'), 'password123')
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
    mockLogin.mockResolvedValue(TEACHER)
    renderLoginPage()

    await fillAndSubmit('old-pw')

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith('teacher@example.com', 'old-pw'))
  })

  it('tells the password manager which field is the username and which the current password', () => {
    renderLoginPage()

    expect(screen.getByPlaceholderText('ten@hcmut.edu.vn')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByPlaceholderText('••••••••')).toHaveAttribute('autocomplete', 'current-password')
  })

  it('sends a lecturer to their courses and an admin to the admin page', async () => {
    mockLogin.mockResolvedValue(TEACHER)
    const { unmount } = renderLoginPage()
    await fillAndSubmit()
    expect(await screen.findByText('Trang giảng viên')).toBeInTheDocument()
    unmount()

    mockLogin.mockResolvedValue({ ...TEACHER, role: 'admin' })
    renderLoginPage()
    await fillAndSubmit()
    expect(await screen.findByText('Trang quản trị')).toBeInTheDocument()
  })

  it('points a person who forgot their password to the admin, by email', () => {
    renderLoginPage()

    const link = screen.getByRole('link', { name: 'Quên mật khẩu? Liên hệ admin để hỗ trợ' })

    expect(link).toHaveAttribute('href', 'mailto:dat.duongtandat385@gmail.com')
  })

  it('links to the sign-up page for a lecturer coming for the first time', () => {
    renderLoginPage()

    expect(screen.getByRole('link', { name: 'Đăng ký' })).toHaveAttribute('href', '/register')
  })

  it('tells a person who has just signed up that they can sign in', () => {
    render(
      <MemoryRouter initialEntries={[{ pathname: '/login', state: { justRegistered: true } }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText(/tạo tài khoản thành công/i)).toBeInTheDocument()
  })
})
