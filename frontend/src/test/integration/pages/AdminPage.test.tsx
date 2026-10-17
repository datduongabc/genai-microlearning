import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AdminPage } from '../../../pages/AdminPage'
import * as api from '../../../api'
import type { AdminUser, FailedJobInfo } from '../../../api'
import * as AuthHookModule from '../../../auth/useAuth'

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

const admin: AdminUser = {
  id: 'admin-1', email: 'admin@example.com', name: 'Quản trị', is_admin: true, is_active: true,
  created_at: '2026-01-01T00:00:00Z', moodle_status: null, course_count: null,
}
const lan: AdminUser = {
  id: 'u-1', email: 'lan@example.com', name: 'Lan Nguyễn', is_admin: false, is_active: true,
  created_at: '2026-01-02T00:00:00Z', moodle_status: 'linked', course_count: 2,
}
const minh: AdminUser = {
  id: 'u-2', email: 'minh@example.com', name: 'Minh Trần', is_admin: false, is_active: false,
  created_at: '2026-01-03T00:00:00Z', moodle_status: 'not_linked', course_count: 0,
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
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r', user: { id: 'admin-1', email: 'admin@example.com', name: 'Quản trị', is_admin: true },
    login: vi.fn(), logout: vi.fn(),
  })
  vi.mocked(api.listUsers).mockResolvedValue([admin, lan, minh])
  vi.mocked(api.listFailedJobs).mockResolvedValue([])
})

describe('AdminPage - accounts', () => {
  it('lists every account with its state and Moodle link status', async () => {
    renderPage()

    const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
    expect(within(row).getByText('↗ Đã liên kết')).toBeInTheDocument()
    const locked = screen.getByText('minh@example.com').closest('li')!
    await userEvent.click(within(locked).getByText('⋯'))
    expect(within(locked).getByText('Chưa liên kết')).toBeInTheDocument()
    // The admin themselves have no Moodle status.
    expect(within(screen.getByText('admin@example.com').closest('li')!).queryByText(/moodle/i)).not.toBeInTheDocument()
  })

  it('offers account creation and password reset without automatic Moodle linking', async () => {
    renderPage()
    const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
    expect(screen.getByRole('button', { name: /Tạo tài khoản/ })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Đặt lại mật khẩu' })).toBeInTheDocument()
  })

  it('closes the row action menu after an action is chosen', async () => {
    renderPage()
    const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
    const menu = within(row).getByText('⋯').closest('details')!
    expect(menu).toHaveAttribute('open')
    await userEvent.click(within(row).getByRole('button', { name: 'Đặt lại mật khẩu' }))
    expect(menu).not.toHaveAttribute('open')
    expect(within(row).getByLabelText('Mật khẩu mới của lan@example.com')).toBeInTheDocument()
  })

  it('deactivates and reactivates an account after confirmation', async () => {
    vi.mocked(api.setUserActive).mockResolvedValue(lan)
    renderPage()
    const active = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(active).getByText('⋯'))
    const locked = screen.getByText('minh@example.com').closest('li')!
    await userEvent.click(within(locked).getByText('⋯'))

    await userEvent.click(within(active).getByRole('button', { name: 'Vô hiệu hóa' }))
    await waitFor(() => expect(api.setUserActive).toHaveBeenCalledWith('u-1', false))
    await userEvent.click(within(locked).getByRole('button', { name: 'Kích hoạt lại' }))
    await waitFor(() => expect(api.setUserActive).toHaveBeenCalledWith('u-2', true))
  })

  it('does not offer admin deactivation', async () => {
    renderPage()
    const own = (await screen.findByText('admin@example.com')).closest('li')!
    await userEvent.click(within(own).getByText('⋯'))

    expect(within(own).getByRole('button', { name: 'Vô hiệu hóa' })).toBeDisabled()
  })

  it('cancels activation changes when confirmation is refused', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage()
    const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
    await userEvent.click(within(row).getByRole('button', { name: 'Vô hiệu hóa' }))
    expect(api.setUserActive).not.toHaveBeenCalled()
    expect(within(row).queryByRole('button', { name: 'Xóa' })).not.toBeInTheDocument()
  })

  it('lists active accounts by default with only active and deactivated filters', async () => {
    renderPage()
    await screen.findByText('lan@example.com')
    expect(api.listUsers).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: 'Đang hoạt động' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Đã vô hiệu hóa' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('requests only deactivated accounts after changing the filter', async () => {
    renderPage()
    await screen.findByText('lan@example.com')
    vi.mocked(api.listUsers).mockResolvedValue([minh])
    await userEvent.click(screen.getByRole('button', { name: 'Đã vô hiệu hóa' }))
    await waitFor(() => expect(api.listUsers).toHaveBeenLastCalledWith(false))
    await waitFor(() => expect(screen.queryByText('lan@example.com')).not.toBeInTheDocument())
    const row = (await screen.findByText('minh@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
    expect(within(row).getByRole('button', { name: 'Kích hoạt lại' })).toBeEnabled()
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
    await userEvent.click(await screen.findByRole('tab', { name: 'Job lỗi' }))
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

    expect(await screen.findByText('j1.pdf')).toBeInTheDocument()
    expect(screen.getByText('Minh Trần')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Chi tiết j2.pdf' }))
    expect(screen.getByText(/minh@example.com/)).toBeInTheDocument()
    expect(screen.getAllByText('error of j2')[0]).toBeInTheDocument()
    expect(screen.getByText(/Bước: generate/)).toBeInTheDocument()
  })

  it('asks the server for one job type when the filter changes', async () => {
    await openFailures()
    await userEvent.click(await screen.findByRole('button', { name: 'Sinh mô-đun' }))

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
    await screen.findByText('j1.pdf')
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

it('confirms admin Moodle unlink for the selected user and refreshes their status', async () => {
  renderPage()
  const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
  const button = within(row).getByRole('button', { name: 'Hủy liên kết Moodle' })
  expect(within(screen.getByText('minh@example.com').closest('li')!).queryByRole('button', { name: 'Hủy liên kết Moodle' })).not.toBeInTheDocument()
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  await userEvent.click(button)
  expect(api.unlinkUserMoodleAccount).not.toHaveBeenCalled()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.listUsers).mockResolvedValue([admin, { ...lan, moodle_status: 'not_linked' }, minh])
  await userEvent.click(within(row).getByText('⋯'))
  await userEvent.click(button)
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(lan.email))
  expect(api.unlinkUserMoodleAccount).toHaveBeenCalledWith(lan.id)
  expect(await within(row).findByText('Chưa liên kết')).toBeInTheDocument()
  expect(within(row).queryByRole('button', { name: 'Hủy liên kết Moodle' })).not.toBeInTheDocument()
})

it('shows an admin unlink failure without changing the user link state', async () => {
  vi.mocked(api.unlinkUserMoodleAccount).mockRejectedValueOnce(new Error('offline'))
  renderPage()
  const row = (await screen.findByText('lan@example.com')).closest('li')!
    await userEvent.click(within(row).getByText('⋯'))
  await userEvent.click(within(row).getByRole('button', { name: 'Hủy liên kết Moodle' }))
  expect(await screen.findByText('Không hủy được liên kết Moodle.')).toBeInTheDocument()
  expect(within(row).getByText('↗ Đã liên kết')).toBeInTheDocument()
})

it('moves admin tabs with the keyboard and returns Home to accounts', async () => {
  renderPage()
  const accounts = screen.getByRole('tab', { name: 'Tài khoản' })
  accounts.focus()
  await userEvent.keyboard('{ArrowRight}')
  expect(screen.getByRole('tab', { name: 'Job lỗi' })).toHaveFocus()
  expect(await screen.findByText(/không có job nào thất bại/i)).toBeVisible()
  await userEvent.keyboard('{Home}')
  expect(accounts).toHaveFocus()
  expect(await screen.findByText('lan@example.com')).toBeVisible()
})
