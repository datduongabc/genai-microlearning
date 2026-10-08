import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { RegisterPage } from './RegisterPage'

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

async function fill({ name = 'Nguyen Van A', password = 'a-long-passphrase', confirm = undefined as string | undefined } = {}) {
  confirm ??= password
  await userEvent.type(screen.getByPlaceholderText('Nguyễn Văn A'), name)
  await userEvent.type(screen.getByPlaceholderText('ten@example.com'), 'teacher@example.com')
  const [passwordField, confirmField] = screen.getAllByPlaceholderText('••••••••')
  await userEvent.type(passwordField, password)
  await userEvent.type(confirmField, confirm)
  await userEvent.click(screen.getByRole('button', { name: /tạo tài khoản/i }))
}

beforeEach(() => {
  mockNavigate.mockReset()
  vi.restoreAllMocks()
  render(
    <MemoryRouter>
      <RegisterPage />
    </MemoryRouter>,
  )
})

describe('RegisterPage', () => {
  it('signs the person up and sends them to the login page', async () => {
    const register = vi.spyOn(api, 'register').mockResolvedValue(undefined as never)

    await fill()

    await waitFor(() => expect(register).toHaveBeenCalledWith('Nguyen Van A', 'teacher@example.com', 'a-long-passphrase'))
    expect(mockNavigate).toHaveBeenCalledWith('/login', { state: { justRegistered: true } })
  })

  it('points out a password that is too short without asking the server', async () => {
    const register = vi.spyOn(api, 'register')

    await fill({ password: 'short' })

    expect(await screen.findByText(/tối thiểu 8 ký tự/i)).toBeInTheDocument()
    expect(register).not.toHaveBeenCalled()
  })

  it('shows the reason when the server refuses the password', async () => {
    vi.spyOn(api, 'register').mockRejectedValue(
      refusal(422, { detail: [{ msg: 'Value error, Mật khẩu này quá phổ biến và dễ bị đoán, hãy chọn mật khẩu khác.' }] }),
    )

    await fill()

    expect(await screen.findByText(/quá phổ biến/i)).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('shows what the server said when the email is already taken', async () => {
    vi.spyOn(api, 'register').mockRejectedValue(refusal(409, { detail: 'Email đã được đăng ký' }))

    await fill()

    expect(await screen.findByText('Email đã được đăng ký')).toBeInTheDocument()
  })

  it('tells the password manager to offer a new password rather than a saved one', () => {
    const [passwordField, confirmField] = screen.getAllByPlaceholderText('••••••••')

    expect(passwordField).toHaveAttribute('autocomplete', 'new-password')
    expect(confirmField).toHaveAttribute('autocomplete', 'new-password')
  })
})
