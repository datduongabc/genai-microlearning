import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from '../../../components/AppShell'
import * as AuthHookModule from '../../../auth/useAuth'
import * as api from '../../../api'

vi.mock('../../../api', async importOriginal => ({
  ...await importOriginal<typeof import('../../../api')>(),
  getMoodleAccount: vi.fn(), unlinkMoodleAccount: vi.fn(), requestMoodleLink: vi.fn(),
}))
const logout = vi.fn()
beforeEach(() => {
  vi.restoreAllMocks()
  logout.mockReset()
  vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'linked', email: 'moodle@school.edu' })
  vi.mocked(api.unlinkMoodleAccount).mockResolvedValue(undefined)
})
function renderShell(admin = false, route = '/courses') {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r', user: { id: '1', email: 'a@b.com', name: 'An', is_admin: admin },
    login: vi.fn(), logout,
  })
  return render(<MemoryRouter initialEntries={[route]}><AppShell title="Trang">content</AppShell></MemoryRouter>)
}

describe('AppShell', () => {
  it('shows lecturer navigation, a brand home link, and the active route', () => {
    renderShell()
    expect(screen.getByRole('link', { name: /genailms/i })).toHaveAttribute('href', '/courses')
    expect(screen.getByRole('link', { name: 'Môn học' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Chấm nghiên cứu' })).toHaveAttribute('href', '/studies')
    expect(screen.queryByRole('link', { name: 'Quản trị' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Moodle/)).not.toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveTextContent('content')
  })
  it('gives admins their home and admin navigation', async () => {
    renderShell(true, '/admin')
    expect(screen.getByRole('link', { name: /genailms/i })).toHaveAttribute('href', '/admin')
    expect(screen.getByRole('link', { name: 'Quản trị' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('link', { name: 'Môn học' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Quản trị viên/ }))
    expect(api.getMoodleAccount).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }))
    expect(logout).toHaveBeenCalledOnce()
  })
  it('supports keyboard opening, arrow navigation, Escape and focus restoration', async () => {
    renderShell()
    const trigger = screen.getByRole('button', { name: /Giảng viên/ })
    trigger.focus()
    await userEvent.keyboard('{Enter}')
    await screen.findByText('moodle@school.edu')
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('button', { name: 'Hủy liên kết Moodle…' })).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(screen.getByRole('button', { name: 'Đăng xuất' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
  it('closes when clicking outside', async () => {
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /Giảng viên/ }))
    await screen.findByText('moodle@school.edu')
    await userEvent.click(screen.getByRole('heading', { name: 'Trang' }))
    expect(screen.queryByRole('region', { name: 'Tài khoản' })).not.toBeInTheDocument()
  })
  it('requires confirmation before unlinking and notifies other Moodle views on success', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const changed = vi.fn()
    window.addEventListener(api.MOODLE_ACCOUNT_CHANGED_EVENT, changed)
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /Giảng viên/ }))
    const unlink = await screen.findByRole('button', { name: 'Hủy liên kết Moodle…' })
    await userEvent.click(unlink)
    expect(api.unlinkMoodleAccount).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'not_linked', email: '' })
    await userEvent.click(unlink)
    await waitFor(() => expect(api.unlinkMoodleAccount).toHaveBeenCalledOnce())
    expect(changed).toHaveBeenCalledOnce()
    expect(await screen.findByRole('button', { name: 'Yêu cầu liên kết' })).toBeInTheDocument()
    window.removeEventListener(api.MOODLE_ACCOUNT_CHANGED_EVENT, changed)
  })
  it('keeps the linked account visible and reports unlink failures', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.unlinkMoodleAccount).mockRejectedValue(new Error('offline'))
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /Giảng viên/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Hủy liên kết Moodle…' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Không hủy được liên kết Moodle.')
    expect(screen.getByText('moodle@school.edu')).toBeInTheDocument()
  })
  it('keeps native keyboard editing in the Moodle email field', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'not_linked', email: '' })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /Giảng viên/ }))
    const input = await screen.findByLabelText('Email Moodle')
    await userEvent.type(input, 'gv@truong.edu.vn{Home}')
    expect(input).toHaveFocus()
    for (const key of ['Home', 'End', 'ArrowUp', 'ArrowDown']) expect(fireEvent.keyDown(input, { key })).toBe(true)
    expect(input).toHaveFocus()
  })
  it('hands a pending account that becomes linked to the menu unlink action', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'pending', email: 'moodle@school.edu' })
    renderShell()
    await userEvent.click(screen.getByRole('button', { name: /Giảng viên/ }))
    expect(await screen.findByText('Chờ xác nhận trong Moodle')).toBeInTheDocument()
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'linked', email: 'moodle@school.edu' })
    act(() => { window.dispatchEvent(new Event(api.MOODLE_ACCOUNT_CHANGED_EVENT)) })
    const unlink = await screen.findByRole('button', { name: 'Hủy liên kết Moodle…' })
    expect(screen.queryByRole('button', { name: 'Hủy liên kết Moodle' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Tài khoản Moodle' })).not.toBeInTheDocument()
    await userEvent.click(unlink)
    expect(confirm).toHaveBeenCalledWith('Hủy liên kết Moodle? Bạn sẽ không xuất bản lên Moodle được cho đến khi liên kết lại.')
    await waitFor(() => expect(api.unlinkMoodleAccount).toHaveBeenCalledOnce())
  })
  it('keeps keyboard focus in the menu when a pending account becomes linked', async () => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'pending', email: 'moodle@school.edu' })
    renderShell()
    const trigger = screen.getByRole('button', { name: /Giảng viên/ })
    await userEvent.click(trigger)
    screen.getByRole('button', { name: 'Yêu cầu liên kết' }).focus()
    await screen.findByText('Chờ xác nhận trong Moodle')
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ status: 'linked', email: 'moodle@school.edu' })
    act(() => { window.dispatchEvent(new Event(api.MOODLE_ACCOUNT_CHANGED_EVENT)) })
    await screen.findByRole('button', { name: 'Hủy liên kết Moodle…' })
    expect(screen.getByRole('button', { name: 'Đăng xuất' })).toHaveFocus()
    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('button', { name: 'Hủy liên kết Moodle…' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})
