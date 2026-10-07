import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MoodleAccountSection } from './MoodleAccountSection'

function refusal(status: number, detail: string) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data: { detail }, headers: {}, config } as AxiosResponse
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, null, response)
}

async function fillAndSubmit(username = 'lan.nguyen', password = 'Moodle-pass-1') {
  if (username) await userEvent.type(screen.getByLabelText('Tên đăng nhập Moodle'), username)
  if (password) await userEvent.type(screen.getByLabelText('Mật khẩu Moodle'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Liên kết tài khoản Moodle' }))
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
})

describe('MoodleAccountSection', () => {
  it('links the account with the username and password and then shows it as linked', async () => {
    const link = vi.spyOn(api, 'linkMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'linked' })
    const onStatus = vi.fn()
    render(<MoodleAccountSection onStatus={onStatus} />)
    expect(await screen.findByText('Chưa liên kết')).toBeInTheDocument()

    await fillAndSubmit()

    await waitFor(() => expect(link).toHaveBeenCalledExactlyOnceWith('lan.nguyen', 'Moodle-pass-1'))
    expect(await screen.findByText('Đã liên kết')).toBeInTheDocument()
    expect(onStatus).toHaveBeenLastCalledWith('linked')
    expect(screen.queryByLabelText('Mật khẩu Moodle')).not.toBeInTheDocument()
  })

  it('says the credentials are wrong, stays unlinked and clears the password but keeps the username', async () => {
    vi.spyOn(api, 'linkMoodleAccount').mockRejectedValue(refusal(422, 'Tên đăng nhập hoặc mật khẩu Moodle không đúng.'))
    render(<MoodleAccountSection />)
    await screen.findByText('Chưa liên kết')

    await fillAndSubmit('lan.nguyen', 'not-it')

    expect(await screen.findByText('Tên đăng nhập hoặc mật khẩu Moodle không đúng.')).toBeInTheDocument()
    expect(screen.getByText('Chưa liên kết')).toBeInTheDocument()
    expect(screen.getByLabelText('Mật khẩu Moodle')).toHaveValue('')
    expect(screen.getByLabelText('Tên đăng nhập Moodle')).toHaveValue('lan.nguyen')
  })

  it('needs both fields before it asks the server', async () => {
    const link = vi.spyOn(api, 'linkMoodleAccount')
    render(<MoodleAccountSection />)
    await screen.findByText('Chưa liên kết')

    await fillAndSubmit('lan.nguyen', '')

    expect(await screen.findByText(/nhập mật khẩu moodle/i)).toBeInTheDocument()
    expect(link).not.toHaveBeenCalled()
  })

  it('keeps the password out of autofill and masked', async () => {
    render(<MoodleAccountSection />)
    await screen.findByText('Chưa liên kết')

    expect(screen.getByLabelText('Mật khẩu Moodle')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Mật khẩu Moodle')).toHaveAttribute('autocomplete', 'off')
  })

  it('offers no way to create a Moodle account and shows no first-login pop-up', async () => {
    render(<MoodleAccountSection />)
    await screen.findByText('Chưa liên kết')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByText(/tạo mới|khuyến nghị/i)).not.toBeInTheDocument()
  })

  it('says so when Moodle cannot be checked', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockRejectedValue(new Error('down'))
    render(<MoodleAccountSection />)

    expect(await screen.findByText('Không kiểm tra được Moodle')).toBeInTheDocument()
  })
})
