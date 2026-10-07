import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminPage } from './AdminPage'
import * as api from '../api'
import type { AdminUser, FailedJobInfo } from '../api'
import * as AuthHookModule from '../auth/useAuth'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

const admin: AdminUser = {
  id: 'admin-1', email: 'admin@example.com', name: 'Quản trị', role: 'admin', is_active: true,
  created_at: '2026-01-01T00:00:00Z', moodle_status: null, course_count: null, moodle_course_count: null,
}
const lan: AdminUser = {
  id: 'u-1', email: 'lan@example.com', name: 'Lan Nguyễn', role: 'teacher', is_active: true,
  created_at: '2026-01-02T00:00:00Z', moodle_status: 'linked', course_count: 2, moodle_course_count: 0,
}
const minh: AdminUser = {
  id: 'u-2', email: 'minh@example.com', name: 'Minh Trần', role: 'teacher', is_active: false,
  created_at: '2026-01-03T00:00:00Z', moodle_status: 'not_linked', course_count: 0, moodle_course_count: 0,
}

function failedJob(id: string, overrides: Partial<FailedJobInfo> = {}): FailedJobInfo {
  return {
    id, job_type: 'document_pipeline', step: 'extract', attempts: 5, max_attempts: 5, last_error: `error of ${id}`,
    created_at: '2026-01-01T00:00:00Z', document_filename: `${id}.pdf`, course_name: 'AI 101',
    owner_name: 'Lan Nguyễn', owner_email: 'lan@example.com', module_id: null, ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AdminPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r', user: { id: 'admin-1', email: 'admin@example.com', name: 'Quản trị', role: 'admin' },
    login: vi.fn(), logout: vi.fn(),
  })
  vi.mocked(api.listUsers).mockResolvedValue([admin, lan, minh])
  vi.mocked(api.listFailedJobs).mockResolvedValue([])
})

describe('AdminPage - accounts', () => {
  it('lists every account with its state and Moodle link status', async () => {
    renderPage()

    const row = (await screen.findByText('lan@example.com')).closest('li')!
    expect(within(row).getByText('Đang hoạt động')).toBeInTheDocument()
    expect(within(row).getByText('Đã liên kết Moodle')).toBeInTheDocument()
    const locked = screen.getByText('minh@example.com').closest('li')!
    expect(within(locked).getByText('Đã khóa')).toBeInTheDocument()
    expect(within(locked).getByText('Chưa liên kết Moodle')).toBeInTheDocument()
    // The admin themselves have no Moodle status.
    expect(within(screen.getByText('admin@example.com').closest('li')!).queryByText(/moodle/i)).not.toBeInTheDocument()
  })

  it('has no way to create a lecturer or set a password: lecturers come from Moodle', async () => {
    renderPage()
    const row = (await screen.findByText('lan@example.com')).closest('li')!

    expect(screen.queryByRole('button', { name: 'Tạo tài khoản' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Mật khẩu ban đầu')).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: 'Đặt lại mật khẩu' })).not.toBeInTheDocument()
  })

  it('locks an active account and unlocks a locked one', async () => {
    vi.mocked(api.setUserActive).mockResolvedValue(lan)
    renderPage()
    const active = (await screen.findByText('lan@example.com')).closest('li')!
    const locked = screen.getByText('minh@example.com').closest('li')!

    await userEvent.click(within(active).getByRole('button', { name: 'Khóa' }))
    await waitFor(() => expect(api.setUserActive).toHaveBeenCalledWith('u-1', false))
    await userEvent.click(within(locked).getByRole('button', { name: 'Mở khóa' }))
    await waitFor(() => expect(api.setUserActive).toHaveBeenCalledWith('u-2', true))
  })

  it('does not offer the admin a way to lock themselves', async () => {
    renderPage()
    const own = (await screen.findByText('admin@example.com')).closest('li')!

    expect(within(own).getByRole('button', { name: 'Khóa' })).toBeDisabled()
  })

  it('deletes a lecturer without courses after a confirmation and reloads the list', async () => {
    vi.mocked(api.deleteUser).mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()
    const row = (await screen.findByText('minh@example.com')).closest('li')!

    await userEvent.click(within(row).getByRole('button', { name: 'Xóa' }))

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('minh@example.com'))
    await waitFor(() => expect(api.deleteUser).toHaveBeenCalledWith('u-2'))
    expect(await screen.findByText('Đã xóa tài khoản minh@example.com.')).toBeInTheDocument()
    expect(api.listUsers).toHaveBeenCalledTimes(2)
  })

  it('does not delete when the admin cancels the confirmation', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage()
    const row = (await screen.findByText('minh@example.com')).closest('li')!

    await userEvent.click(within(row).getByRole('button', { name: 'Xóa' }))

    expect(api.deleteUser).not.toHaveBeenCalled()
  })

  it('explains and disables delete for a lecturer with courses, and offers none for an admin', async () => {
    renderPage()
    const withCourses = (await screen.findByText('lan@example.com')).closest('li')!
    const own = screen.getByText('admin@example.com').closest('li')!

    const button = within(withCourses).getByRole('button', { name: 'Xóa' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('2 môn học'))
    expect(within(withCourses).getByText(/2 môn học/)).toBeInTheDocument()
    expect(within(own).queryByRole('button', { name: 'Xóa' })).not.toBeInTheDocument()
  })

  it('explains and disables delete for a lecturer who holds a Moodle course but no course here', async () => {
    const hoa: AdminUser = {
      id: 'u-3', email: 'hoa@example.com', name: 'Hoa Lê', role: 'teacher', is_active: true,
      created_at: '2026-01-04T00:00:00Z', moodle_status: 'linked', course_count: 0, moodle_course_count: 1,
    }
    vi.mocked(api.listUsers).mockResolvedValue([admin, hoa])
    renderPage()
    const row = (await screen.findByText('hoa@example.com')).closest('li')!

    const button = within(row).getByRole('button', { name: 'Xóa' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('1 môn học Moodle'))
    expect(within(row).getByText(/1 môn học Moodle/)).toBeInTheDocument()
  })

  it('shows what the server said when the delete is refused', async () => {
    vi.mocked(api.deleteUser).mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'Không thể xóa giảng viên đang có môn học.' } },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()
    const row = (await screen.findByText('minh@example.com')).closest('li')!

    await userEvent.click(within(row).getByRole('button', { name: 'Xóa' }))

    expect(await screen.findByText('Không thể xóa giảng viên đang có môn học.')).toBeInTheDocument()
  })

  it('says so when the list cannot be loaded', async () => {
    vi.mocked(api.listUsers).mockRejectedValue(new Error('down'))
    renderPage()

    expect(await screen.findByText(/không tải được danh sách tài khoản/i)).toBeInTheDocument()
  })
})

describe('AdminPage - failed jobs', () => {
  async function openFailures() {
    renderPage()
    await userEvent.click(await screen.findByRole('tab', { name: 'Theo dõi lỗi' }))
  }

  it('shows a friendly message when nothing failed', async () => {
    await openFailures()

    expect(await screen.findByText(/không có job nào thất bại/i)).toBeInTheDocument()
  })

  it('lists the failed jobs of every lecturer with who, course, file and error', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([
      failedJob('j1'),
      failedJob('j2', { owner_name: 'Minh Trần', owner_email: 'minh@example.com', job_type: 'module_generation', step: 'generate' }),
    ])
    await openFailures()

    expect(await screen.findByText('AI 101 / j1.pdf')).toBeInTheDocument()
    expect(screen.getByText('Minh Trần (minh@example.com)')).toBeInTheDocument()
    expect(screen.getByText('error of j2')).toBeInTheDocument()
    expect(screen.getByText(/sinh mô-đun — generate/i)).toBeInTheDocument()
  })

  it('asks the server for one job type when the filter changes', async () => {
    await openFailures()
    await userEvent.selectOptions(await screen.findByLabelText('Loại job:'), 'module_generation')

    await waitFor(() => expect(api.listFailedJobs).toHaveBeenLastCalledWith('module_generation'))
  })

  it('retries one job from its own button', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([failedJob('j1'), failedJob('j2')])
    vi.mocked(api.retryFailedJobs).mockResolvedValue({ retried: ['j1'], skipped: [] })
    await openFailures()

    await userEvent.click(await screen.findByRole('button', { name: 'Thử lại j1.pdf' }))

    await waitFor(() => expect(api.retryFailedJobs).toHaveBeenCalledWith(['j1']))
    expect(await screen.findByText(/đã đưa 1 job vào hàng đợi/i)).toBeInTheDocument()
  })

  it('retries the picked jobs together, or all of them with select-all', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([failedJob('j1'), failedJob('j2'), failedJob('j3')])
    vi.mocked(api.retryFailedJobs).mockResolvedValue({ retried: ['j1', 'j3'], skipped: ['j2'] })
    await openFailures()
    await screen.findByText('AI 101 / j1.pdf')
    expect(screen.getByRole('button', { name: 'Thử lại đã chọn' })).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Chọn j1.pdf' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chọn j3.pdf' }))
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại 2 job đã chọn' }))

    await waitFor(() => expect(api.retryFailedJobs).toHaveBeenCalledWith(['j1', 'j3']))
    expect(await screen.findByText(/1 job bị bỏ qua/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Chọn tất cả' }))
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại 3 job đã chọn' }))
    await waitFor(() => expect(api.retryFailedJobs).toHaveBeenLastCalledWith(['j1', 'j2', 'j3']))
  })

  it('reloads the list after a retry so what ran again is gone', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValueOnce([failedJob('j1')]).mockResolvedValue([])
    vi.mocked(api.retryFailedJobs).mockResolvedValue({ retried: ['j1'], skipped: [] })
    await openFailures()

    await userEvent.click(await screen.findByRole('button', { name: 'Thử lại j1.pdf' }))

    expect(await screen.findByText(/không có job nào thất bại/i)).toBeInTheDocument()
  })
})
