import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createMemoryRouter, createRoutesFromElements, Route, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FlashcardEditorPage } from '../../../pages/FlashcardEditorPage'
import { AuthProvider } from '../../../auth/AuthContext'
import { ProtectedRoute } from '../../../auth/ProtectedRoute'
import * as api from '../../../api'
import type { FlashcardCard, FlashcardSetDetail } from '../../../api'

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['apiErrorMessage', 'api'].includes(name) ? vi.fn() : value,
    ]),
  )
})

const card = (n: number, extra: Partial<FlashcardCard> = {}): FlashcardCard => ({
  front: `Mặt trước ${n}`, back: `Mặt sau ${n}`, citations: ['chunk-1'], author: 'model', flag: null,
  sources: [{ chunk_id: 'chunk-1', locator: 'bai-giang.pdf, trang 3', content: 'Một mảng là dãy ô liên tiếp.' }], ...extra,
})

const draft = (cards: FlashcardCard[], extra: Partial<FlashcardSetDetail> = {}): FlashcardSetDetail => ({
  id: 'set-1', course_id: 'course-1', document_id: 'doc-1', focus: 'Mảng', output_language: 'vi', model_alias: 'gemini-flash',
  status: 'draft', card_count: cards.length, published_at: null, created_at: '2026-01-01T00:00:00Z',
  draft_version: 3,
  cards, ...extra,
})

// A data router, as in main.tsx: the leave guard needs one for useBlocker. The other pages are
// stand-ins, to tell where a navigation went.
function renderPage() {
  const router = createMemoryRouter(createRoutesFromElements(
    <>
      <Route path="/login" element={<p>Trang đăng nhập</p>} />
      <Route path="/courses" element={<p>Danh sách môn học</p>} />
      <Route path="/courses/:courseId" element={<p>Trang môn học</p>} />
      <Route element={<ProtectedRoute />}>
        <Route path="/flashcard-sets/:id" element={<FlashcardEditorPage />} />
      </Route>
    </>,
  ), { initialEntries: ['/courses', '/flashcard-sets/set-1'], initialIndex: 1 })
  return { router, ...render(<AuthProvider><RouterProvider router={router} /></AuthProvider>) }
}

const axiosFailure = (status: number, detail: string) => {
  const config = { headers: {} } as InternalAxiosRequestConfig
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null,
    { status, statusText: '', data: { detail }, headers: {}, config } as AxiosResponse)
}

beforeEach(() => {
  localStorage.setItem('auth', JSON.stringify({
    token: 't', refreshToken: 'r', user: { id: 'u1', email: 'lan@x.edu', name: 'Lan', can_author: true, is_admin: false },
  }))
  vi.clearAllMocks()
})

afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
})

// The card's own "Sửa": the editor opens one card at a time, as the module editor opens one part.
const openCard = async (n: number) => userEvent.click(await screen.findByRole('button', { name: `Sửa thẻ ${n}` }))

describe('FlashcardEditorPage', () => {
  it('shows flags, hides lecturer uncited flag, toggles source marker, truncates long title, and navigates cleanly when unchanged', async () => {
    const focus = 'Ví dụ: các định nghĩa trong chương 2 '.repeat(6).trim()
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([
      card(1),
      // An older save stored "Chưa có nguồn" on the lecturer's card; they have no way to cite, so it is not shown.
      card(2, { flag: 'Chưa có nguồn', citations: [], sources: [], author: 'lecturer' }),
      card(3, { flag: 'Có thể không khớp tài liệu', sources: [] }),
    ], { focus }))
    const confirm = vi.spyOn(window, 'confirm')

    renderPage()

    // The cards, not the breadcrumb's links above the title.
    const cards = within(await screen.findByRole('list', { name: 'Các thẻ' })).getAllByRole('listitem')
    expect(screen.queryByText('Chưa có nguồn')).not.toBeInTheDocument()
    expect(within(cards[2]).getByText('Có thể không khớp tài liệu')).toBeInTheDocument()
    // Static text: a screen reader reads it with the card instead of being interrupted by it.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading.textContent!.length).toBeLessThanOrEqual(60)
    expect(heading.textContent).toMatch(/…$/)
    expect(heading).toHaveAttribute('title', `Flashcard: ${focus}`)

    const sources = screen.getByRole('group', { name: 'Nguồn của thẻ 1' })
    const marker = within(sources).getByRole('button', { name: 'Nguồn 1: bai-giang.pdf, trang 3' })
    expect(marker).toHaveTextContent('[1]')
    expect(marker).toHaveAttribute('aria-expanded', 'false')
    expect(within(sources).queryByText('Một mảng là dãy ô liên tiếp.')).not.toBeInTheDocument()
    await userEvent.click(marker)
    expect(marker).toHaveAttribute('aria-expanded', 'true')
    expect(within(sources).getByText('Một mảng là dãy ô liên tiếp.')).toBeVisible()
    await userEvent.click(marker)
    expect(within(sources).queryByText('Một mảng là dãy ô liên tiếp.')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Nguồn của thẻ 2' })).not.toBeInTheDocument()

    await userEvent.click(within(screen.getByRole('navigation', { name: 'Điều hướng chính' })).getByRole('link', { name: 'Môn học' }))
    expect(confirm).not.toHaveBeenCalled()
    expect(await screen.findByText('Danh sách môn học')).toBeInTheDocument()
  })

  // Fifteen cards are rendered again at every step: 1.3 s alone, and past the default 5 s when the
  // whole suite shares the machine.
  it('adds a card, saved at once, and stops at 15', async () => {
    const fourteen = Array.from({ length: 14 }, (_, i) => card(i + 1))
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft(fourteen))
    vi.mocked(api.saveFlashcards).mockImplementation(async (_id, _version, cards) =>
      draft(cards.map((c, i) => card(i + 1, { ...c, author: 'lecturer' }))),
    )

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Thêm thẻ' }))
    const save = screen.getByRole('button', { name: 'Lưu' })
    expect(save).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 15'), 'Câu hỏi mới')
    await userEvent.type(screen.getByLabelText('Mặt sau thẻ 15'), 'Trả lời mới')
    await userEvent.click(save)

    await waitFor(() => expect(api.saveFlashcards).toHaveBeenCalledTimes(1))
    const [id, version, sent] = vi.mocked(api.saveFlashcards).mock.calls[0]
    expect(id).toBe('set-1')
    // The version the editor read, so a save made on an older copy is refused.
    expect(version).toBe(3)
    expect(sent).toHaveLength(15)
    expect(sent[0]).toEqual({ front: 'Mặt trước 1', back: 'Mặt sau 1', citations: ['chunk-1'] })
    expect(sent[14]).toEqual({ front: 'Câu hỏi mới', back: 'Trả lời mới', citations: [] })
    expect(await screen.findByText('Đã thêm thẻ.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thêm thẻ' })).toBeDisabled()
  })

  it('validates, cancels, handles 409 conflict, saves an edited card, and rewrites the set from a request', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1), card(2)]))
    vi.mocked(api.saveFlashcards)
      .mockRejectedValueOnce(axiosFailure(409, 'Bộ thẻ đã được lưu ở nơi khác. Hãy tải lại trang rồi sửa tiếp.'))
      .mockResolvedValueOnce(draft([card(1, { front: 'Sửa rồi', author: 'lecturer' }), card(2)], { draft_version: 3 }))
    vi.mocked(api.reviseFlashcardSet).mockResolvedValue({
      ...draft([card(1, { front: 'Sửa rồi', author: 'lecturer' }), card(2, { back: 'Ngắn hơn' })], { draft_version: 4 }),
      reply: 'Đã rút gọn thẻ 2.',
      changed: true,
    })

    renderPage()
    const request = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    const send = screen.getByRole('button', { name: 'Gửi yêu cầu' })
    expect(send).toBeDisabled()
    await userEvent.type(request, 'Rút gọn thẻ 2')

    await openCard(1)
    expect(send).toBeDisabled()
    // One card at a time: the others cannot be opened meanwhile.
    expect(screen.getByRole('button', { name: 'Sửa thẻ 2' })).toBeDisabled()

    await userEvent.clear(screen.getByLabelText('Mặt sau thẻ 1'))
    expect(screen.getByRole('button', { name: 'Lưu' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    expect(api.saveFlashcards).not.toHaveBeenCalled()
    expect(screen.getByText('Mặt trước 1')).toBeInTheDocument()

    await openCard(1)
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText(/đã được lưu ở nơi khác/)).toBeInTheDocument()
    expect(screen.getByLabelText('Mặt trước thẻ 1')).toHaveValue('Mặt trước 1!')

    const front = screen.getByLabelText('Mặt trước thẻ 1')
    await userEvent.clear(front)
    await userEvent.type(front, 'Sửa rồi')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))

    expect(await screen.findByText('Đã lưu thẻ.')).toBeInTheDocument()
    expect(vi.mocked(api.saveFlashcards).mock.calls[1][2]).toEqual([
      { front: 'Sửa rồi', back: 'Mặt sau 1', citations: ['chunk-1'] },
      { front: 'Mặt trước 2', back: 'Mặt sau 2', citations: ['chunk-1'] },
    ])
    expect(screen.queryByLabelText('Mặt trước thẻ 1')).not.toBeInTheDocument()
    expect(screen.getByText('Sửa rồi')).toBeInTheDocument()

    await userEvent.click(send)
    expect(await screen.findByText('Đã rút gọn thẻ 2.')).toBeInTheDocument()
    expect(api.reviseFlashcardSet).toHaveBeenCalledWith('set-1', 3, 'Rút gọn thẻ 2')
    expect(screen.getByText('Ngắn hơn')).toBeInTheDocument()
    expect(request).toHaveValue('')
  })

  it('grows each side to its text, so a long answer is read whole, not cut by the box', async () => {
    // Live run 2 at 390 px: the back kept 3 rows and its fourth line was cut in half.
    const scrollHeight = vi.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get')
      .mockImplementation(function (this: HTMLTextAreaElement) { return this.value.length > 20 ? 140 : 40 })
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1, { back: 'Một câu trả lời dài hơn ba dòng của ô.' })]))
    renderPage()
    await openCard(1)

    const back = screen.getByLabelText('Mặt sau thẻ 1')
    expect(back.style.height).toBe('140px')
    await userEvent.clear(back)
    await userEvent.type(back, 'Ngắn')
    expect(back.style.height).toBe('40px')
    scrollHeight.mockRestore()
  })

  it('removes a card after asking, saved at once', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1), card(2)]))
    vi.mocked(api.saveFlashcards).mockResolvedValue(draft([card(2)]))
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    const remove = await screen.findByRole('button', { name: 'Xóa thẻ 1' })
    await userEvent.click(remove)
    expect(api.saveFlashcards).not.toHaveBeenCalled()
    await userEvent.click(remove)

    expect(await screen.findByText('Đã xóa thẻ.')).toBeInTheDocument()
    expect(vi.mocked(api.saveFlashcards).mock.calls[0][2]).toEqual([
      { front: 'Mặt trước 2', back: 'Mặt sau 2', citations: ['chunk-1'] },
    ])
  })

  it('publishes only when no card is open for editing, and stops publishing when clicked again', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.publishFlashcardSet)
      .mockResolvedValueOnce(draft([card(1)], { published_at: '2026-01-02T00:00:00Z' }))
      .mockResolvedValueOnce(draft([card(1)]))

    renderPage()
    await openCard(1)
    expect(screen.getByRole('button', { name: 'Giao cho sinh viên' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await userEvent.click(screen.getByRole('button', { name: 'Giao cho sinh viên' }))

    expect(api.publishFlashcardSet).toHaveBeenCalledWith('set-1', true)
    const unpublish = await screen.findByRole('button', { name: 'Ngừng giao' })
    await userEvent.click(unpublish)

    expect(api.publishFlashcardSet).toHaveBeenLastCalledWith('set-1', false)
    expect(await screen.findByRole('button', { name: 'Giao cho sinh viên' })).toBeInTheDocument()
  })

  it('cannot publish an empty set', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([]))

    renderPage()

    expect(await screen.findByRole('button', { name: 'Giao cho sinh viên' })).toBeDisabled()
  })

  it('follows a generating set until it is a draft', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.getFlashcardSet)
      .mockResolvedValueOnce(draft([], { status: 'generating' }))
      .mockResolvedValue(draft([card(1)]))

    renderPage()
    expect(await screen.findByText('Đang tạo flashcard…')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await screen.findByText('Mặt trước 1')).toBeInTheDocument()
  })

  it('says a failed generation failed', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([], { status: 'failed' }))

    renderPage()

    expect(await screen.findByText(/Tạo flashcard thất bại/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thêm thẻ' })).not.toBeInTheDocument()
  })

  it('shows a missing set on 404, and a failed load with retry on 500', async () => {
    vi.mocked(api.getFlashcardSet).mockRejectedValueOnce(axiosFailure(404, 'nope'))
    const { unmount } = renderPage()
    expect(await screen.findByText('Không tìm thấy bộ flashcard.')).toBeInTheDocument()
    unmount()

    vi.mocked(api.getFlashcardSet)
      .mockRejectedValueOnce(axiosFailure(500, 'boom'))
      .mockResolvedValue(draft([card(1)]))
    renderPage()
    expect(await screen.findByText('Không tải được bộ flashcard.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }))
    expect(await screen.findByText('Mặt trước 1')).toBeInTheDocument()
  })

  it('confirms deletion and leaves without a leave prompt even with unsaved edits', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.deleteFlashcardSet).mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await openCard(1)
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    const del = screen.getByRole('button', { name: 'Xóa bộ flashcard' })
    await userEvent.click(del)
    expect(api.deleteFlashcardSet).not.toHaveBeenCalled()
    await userEvent.click(del)

    expect(api.deleteFlashcardSet).toHaveBeenCalledWith('set-1')
    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('goes back to its course, and says so while still generating', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([], { status: 'generating' }))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Về trang môn học' }))

    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
  })

  it('warns beforeunload, sidebar, and browser Back on unsaved edits, and leaves when confirmed', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(false).mockReturnValueOnce(true)

    const { router } = renderPage()
    await openCard(1)
    const closing = () => {
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }
    // Opened but not changed is nothing to lose.
    expect(closing()).toBe(false)
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    expect(closing()).toBe(true)

    const sidebar = within(screen.getByRole('navigation', { name: 'Điều hướng chính' }))
    await userEvent.click(sidebar.getByRole('link', { name: 'Môn học' }))
    expect(confirm).toHaveBeenCalledWith('Các thay đổi chưa lưu sẽ mất. Rời trang?')
    expect(screen.queryByText('Danh sách môn học')).not.toBeInTheDocument()
    expect((screen.getByLabelText('Mặt trước thẻ 1') as HTMLTextAreaElement).value).toContain('!')

    await act(() => router.navigate(-1))
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(screen.getByLabelText('Mặt trước thẻ 1')).toBeInTheDocument()

    await act(() => router.navigate(-1))
    expect(await screen.findByText('Danh sách môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(3)
  })

  it('asks before logging out with unsaved edits, once, and logs out when told to', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await openCard(1)
    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Tài khoản Lan/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Mặt trước thẻ 1')).toBeInTheDocument()
    expect(api.logout).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }))
    expect(await screen.findByText('Trang đăng nhập')).toBeInTheDocument()
    expect(api.logout).toHaveBeenCalledWith('r')
    // The redirect to the login page that follows is not asked about a second time.
    expect(confirm).toHaveBeenCalledTimes(2)
  })
})
