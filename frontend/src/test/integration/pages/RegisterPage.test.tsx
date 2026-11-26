import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../../api'
import { RegisterPage } from '../../../pages/RegisterPage'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}))

function refusal(status: number, data: unknown) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data, headers: {}, config } as AxiosResponse
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, null, response)
}

function renderRegisterPage() {
  return render(
    <MemoryRouter>
      <RegisterPage />
    </MemoryRouter>,
  )
}

async function fill({ name = 'Nguyen Van A', password = 'a-long-passphrase', confirm = undefined as string | undefined } = {}) {
  confirm ??= password
  await userEvent.type(screen.getByLabelText('Họ và tên'), name)
  await userEvent.type(screen.getByLabelText('Email'), 'teacher@example.com')
  const passwordField = screen.getByLabelText('Mật khẩu')
  const confirmField = screen.getByLabelText('Nhập lại mật khẩu')
  await userEvent.type(passwordField, password)
  await userEvent.type(confirmField, confirm)
  await userEvent.click(screen.getByRole('button', { name: /tạo tài khoản/i }))
}

beforeEach(() => {
  mockNavigate.mockReset()
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RegisterPage', () => {
  it('tells the password manager to offer a new password and signs the person up', async () => {
    const register = vi.spyOn(api, 'register').mockResolvedValue(undefined as never)
    renderRegisterPage()

    expect(screen.getByLabelText('Mật khẩu')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByLabelText('Nhập lại mật khẩu')).toHaveAttribute('autocomplete', 'new-password')

    await fill()

    await waitFor(() => expect(register).toHaveBeenCalledWith('Nguyen Van A', 'teacher@example.com', 'a-long-passphrase'))
    expect(mockNavigate).toHaveBeenCalledWith('/login', { state: { justRegistered: true } })
  })

  it('points out a password that is too short without asking the server', async () => {
    const register = vi.spyOn(api, 'register')
    renderRegisterPage()

    await fill({ password: 'short' })

    expect(await screen.findByText(/tối thiểu 8 ký tự/i)).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })

  it('shows the reason when the server refuses the password', async () => {
    vi.spyOn(api, 'register').mockRejectedValue(
      refusal(422, { detail: [{ msg: 'Value error, Mật khẩu này quá phổ biến và dễ bị đoán, hãy chọn mật khẩu khác.' }] }),
    )
    renderRegisterPage()

    await fill()

    expect(await screen.findByText(/quá phổ biến/i)).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('shows what the server said when the email is already taken', async () => {
    vi.spyOn(api, 'register').mockRejectedValue(refusal(409, { detail: 'Email đã được đăng ký' }))
    renderRegisterPage()

    await fill()

    expect(await screen.findByText('Email đã được đăng ký')).toBeInTheDocument()
  })

  it('explains instead of showing a form when registration is closed', async () => {
    vi.spyOn(api, 'getAuthOptions').mockResolvedValue({ self_registration: false })
    const register = vi.spyOn(api, 'register')
    renderRegisterPage()

    expect(await screen.findByText(/Đăng ký tài khoản đã đóng/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: /tạo tài khoản/i })).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Đăng nhập' })).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })
})
