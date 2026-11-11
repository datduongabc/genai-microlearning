import { render, screen, waitFor } from '@testing-library/react'
import { act } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../../api'
import { MoodleAccountSection } from '../../../components/MoodleAccountSection'

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('MoodleAccountSection', () => {
  it('shows the account as linked, with nothing to fill in', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'linked' })
    const onStatus = vi.fn()
    render(<MoodleAccountSection onStatus={onStatus} />)

    expect(await screen.findByText('Đã liên kết')).toBeInTheDocument()
    await waitFor(() => expect(onStatus).toHaveBeenLastCalledWith('linked'))
    expect(screen.queryByRole('button', { name: 'Hủy liên kết Moodle' })).not.toBeInTheDocument()
    expect(document.querySelector('input')).toBeNull()
  })

  it('says Moodle is not connected, with no link form, where the environment has no Moodle', async () => {
    // AWS on 2026-11-11: the panel read "Không kiểm tra được Moodle" and the course page "try again later".
    vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'unavailable' })
    const onStatus = vi.fn()
    render(<MoodleAccountSection onStatus={onStatus} />)

    expect(await screen.findByText('Chưa nối với Moodle')).toBeInTheDocument()
    expect(screen.getByText(/mở công cụ từ moodle \(lti\) vẫn dùng được/i)).toBeInTheDocument()
    expect(document.querySelector('input')).toBeNull()
    expect(screen.queryByText(/thử lại sau/i)).not.toBeInTheDocument()
    await waitFor(() => expect(onStatus).toHaveBeenLastCalledWith('unavailable'))
  })

  it('shows that app and Moodle accounts are separate when not linked', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    render(<MoodleAccountSection />)

    expect(await screen.findByText('Chưa liên kết')).toBeInTheDocument()
    expect(screen.getByText(/tài khoản trong ứng dụng và tài khoản moodle/i)).toBeInTheDocument()
    expect(document.querySelector('input[type=password]')).toBeNull()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('says so when Moodle cannot be checked', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockRejectedValue(new Error('down'))
    render(<MoodleAccountSection />)

    expect(await screen.findByText('Không kiểm tra được Moodle')).toBeInTheDocument()
  })

  it('asks the server again when the account may have changed', async () => {
    const get = vi
      .spyOn(api, 'getMoodleAccount')
      .mockResolvedValueOnce({ email: 'lan@x.edu', status: 'linked' })
      .mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    render(<MoodleAccountSection />)
    expect(await screen.findByText('Đã liên kết')).toBeInTheDocument()

    act(() => {
      window.dispatchEvent(new Event(api.MOODLE_ACCOUNT_CHANGED_EVENT))
    })

    expect(await screen.findByText('Chưa liên kết')).toBeInTheDocument()
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
  })
})

it('requests consent by email and stays pending until Moodle confirms', async () => {
  vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'app@example.com', status: 'not_linked' })
  const request = vi.spyOn(api, 'requestMoodleLink').mockResolvedValue({ email: 'moodle@example.com', status: 'pending', confirmation_url: 'http://localhost:48081/local/lms_bridge/confirm.php?request=test' })
  const onStatus = vi.fn()
  render(<MoodleAccountSection onStatus={onStatus} />)
  await userEvent.type(await screen.findByLabelText('Email Moodle'), 'moodle@example.com')
  await userEvent.click(screen.getByRole('button', { name: 'Yêu cầu liên kết' }))
  expect(await screen.findByText('Chờ xác nhận trong Moodle')).toBeInTheDocument()
  expect(request).toHaveBeenCalledWith('moodle@example.com')
  expect(screen.getByText('Nếu có tài khoản Moodle phù hợp, hãy đăng nhập Moodle để xác nhận liên kết.')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Mở Moodle để xác nhận' })).toHaveAttribute('href', expect.stringContaining('/confirm.php'))
  expect(onStatus).toHaveBeenLastCalledWith('pending')
  expect(document.querySelector('input[type=password]')).toBeNull()
})

it('polls pending consent and stops showing the request form once confirmed', async () => {
  vi.useFakeTimers()
  try {
    const get = vi.spyOn(api, 'getMoodleAccount')
      .mockResolvedValueOnce({ email: 'moodle@example.com', status: 'pending', confirmation_url: 'http://localhost:48081/confirm.php' })
      .mockResolvedValue({ email: 'moodle@example.com', status: 'linked' })
    const onStatus = vi.fn()
    render(<MoodleAccountSection onStatus={onStatus} />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('Chờ xác nhận trong Moodle')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('Đã liên kết')).toBeInTheDocument()
    expect(screen.queryByLabelText('Email Moodle')).not.toBeInTheDocument()
    expect(onStatus).toHaveBeenLastCalledWith('linked')
    expect(get).toHaveBeenCalledTimes(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(get).toHaveBeenCalledTimes(2)
  } finally { vi.useRealTimers() }
})

it('offers the request form again when the confirmed Moodle account is already linked elsewhere', async () => {
  vi.useFakeTimers()
  try {
    vi.spyOn(api, 'getMoodleAccount')
      .mockResolvedValueOnce({ email: 'moodle@example.com', status: 'pending', confirmation_url: 'http://localhost:48081/confirm.php' })
      .mockResolvedValue({ email: 'app@example.com', status: 'not_linked', error: 'Tài khoản Moodle đã liên kết với một tài khoản khác.' })
    render(<MoodleAccountSection />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('Chờ xác nhận trong Moodle')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByText('Chưa liên kết')).toBeInTheDocument()
    expect(screen.getByText('Tài khoản Moodle đã liên kết với một tài khoản khác.')).toBeInTheDocument()
    expect(screen.getByLabelText('Email Moodle')).toBeInTheDocument()
  } finally { vi.useRealTimers() }
})
