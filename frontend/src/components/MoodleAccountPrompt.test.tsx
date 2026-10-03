import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MoodleAccountPrompt } from './MoodleAccountPrompt'
import * as api from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})

describe('MoodleAccountPrompt', () => {
  it('recommends linking to a lecturer whose Moodle account is not linked', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })

    render(<MoodleAccountPrompt />)

    expect(await screen.findByRole('dialog', { name: /liên kết tài khoản moodle/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Có (khuyến nghị)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Không' })).toBeInTheDocument()
  })

  it('stays away when the account is linked, or when Moodle cannot be asked', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValueOnce({ email: 'lan@x.edu', status: 'linked' })
    const { unmount } = render(<MoodleAccountPrompt />)
    await waitFor(() => expect(api.getMoodleAccount).toHaveBeenCalledOnce())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    unmount()

    vi.mocked(api.getMoodleAccount).mockRejectedValueOnce(new Error('down'))
    render(<MoodleAccountPrompt />)
    await waitFor(() => expect(api.getMoodleAccount).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('"Có" links the account and closes the pop-up', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    vi.mocked(api.linkMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'linked', created: true })

    render(<MoodleAccountPrompt />)
    await userEvent.click(await screen.findByRole('button', { name: 'Có (khuyến nghị)' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.linkMoodleAccount).toHaveBeenCalledOnce()
  })

  it('keeps the pop-up open and says why when linking fails', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    vi.mocked(api.linkMoodleAccount).mockRejectedValue(new Error('boom'))

    render(<MoodleAccountPrompt />)
    await userEvent.click(await screen.findByRole('button', { name: 'Có (khuyến nghị)' }))

    expect(await screen.findByText(/liên kết tài khoản moodle thất bại/i)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('"Không" dismisses it without linking, and it is not asked again in this session', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })

    const { unmount } = render(<MoodleAccountPrompt />)
    await userEvent.click(await screen.findByRole('button', { name: 'Không' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.linkMoodleAccount).not.toHaveBeenCalled()
    unmount()

    render(<MoodleAccountPrompt />)
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.getMoodleAccount).toHaveBeenCalledOnce()
  })
})
