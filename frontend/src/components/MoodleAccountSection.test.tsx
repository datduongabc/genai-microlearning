import { render, screen, waitFor } from '@testing-library/react'
import { act } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { MoodleAccountSection } from './MoodleAccountSection'

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('MoodleAccountSection', () => {
  it('shows the account as linked, with nothing to fill in', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'linked' })
    const onStatus = vi.fn()
    render(<MoodleAccountSection onStatus={onStatus} />)

    expect(await screen.findByText('Đã liên kết')).toBeInTheDocument()
    expect(onStatus).toHaveBeenLastCalledWith('linked')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(document.querySelector('input')).toBeNull()
  })

  it('tells a lecturer whose account is not linked to log in again with Moodle, without a form', async () => {
    vi.spyOn(api, 'getMoodleAccount').mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    render(<MoodleAccountSection />)

    expect(await screen.findByText('Chưa liên kết')).toBeInTheDocument()
    expect(screen.getByText(/đăng nhập lại bằng tài khoản moodle/i)).toBeInTheDocument()
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
