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
  cost: null, draft_version: 3,
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

describe('FlashcardEditorPage', () => {
  it('shows a warning on a flagged card and on a card without a source', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([
      card(1, { sources: [] }),
      card(2, { flag: 'Chưa có nguồn', citations: [], sources: [] }),
      card(3, { flag: 'Có thể không khớp tài liệu', sources: [] }),
    ]))

    renderPage()

    const cards = await screen.findAllByRole('listitem')
    expect(within(cards[0]).queryByText('Chưa có nguồn')).not.toBeInTheDocument()
    expect(within(cards[1]).getByText('Chưa có nguồn')).toBeInTheDocument()
    expect(within(cards[2]).getByText('Có thể không khớp tài liệu')).toBeInTheDocument()
    // Static text: a screen reader reads it with the card instead of being interrupted by it.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows what the set cost, and says when part of it has no price', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { cost: { usd: 0.0031, calls: 2, priced_calls: 2 } }))
    const first = renderPage()
    expect(await screen.findByText('Chi phí khoảng $0,003')).toBeInTheDocument()
    first.unmount()

    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { cost: { usd: 0.003, calls: 3, priced_calls: 2 } }))
    renderPage()
    expect(await screen.findByText('Chi phí ít nhất $0,003 (chưa rõ giá của 1 lần gọi model)')).toBeInTheDocument()
  })

  it('warns about an uncited card even before the server has flagged it', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1, { citations: [] })]))

    renderPage()

    expect(await screen.findByText('Chưa có nguồn')).toBeInTheDocument()
  })

  // Fifteen cards are rendered again at every step: 1.3 s alone, and past the default 5 s when the
  // whole suite shares the machine.
  it('saves edited cards and stops at 15', { timeout: 15_000 }, async () => {
    const fourteen = Array.from({ length: 14 }, (_, i) => card(i + 1))
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft(fourteen))
    vi.mocked(api.saveFlashcards).mockImplementation(async (_id, _version, cards) =>
      draft(cards.map((c, i) => card(i + 1, { ...c, author: 'lecturer' }))),
    )

    renderPage()
    const add = await screen.findByRole('button', { name: 'Thêm thẻ' })
    expect(add).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Lưu thay đổi' })).toBeDisabled()

    await userEvent.click(add)
    expect(screen.getByRole('button', { name: 'Thêm thẻ' })).toBeDisabled()
    expect(screen.getByText('15 thẻ (tối đa 15)')).toBeInTheDocument()

    // Pasted, not typed: each key re-renders fifteen cards, which took the test past its 5 s limit
    // when the whole suite ran at once.
    const fronts = screen.getAllByLabelText(/^Mặt trước thẻ/)
    await userEvent.click(fronts[14])
    await userEvent.paste('Câu hỏi mới')
    await userEvent.click(screen.getAllByLabelText(/^Mặt sau thẻ/)[14])
    await userEvent.paste('Trả lời mới')
    await userEvent.clear(fronts[0])
    await userEvent.paste('Sửa rồi')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    await waitFor(() => expect(api.saveFlashcards).toHaveBeenCalledTimes(1))
    const [id, version, sent] = vi.mocked(api.saveFlashcards).mock.calls[0]
    expect(id).toBe('set-1')
    // The version the editor read, so a save made on an older copy is refused.
    expect(version).toBe(3)
    expect(sent).toHaveLength(15)
    expect(sent[0]).toEqual({ front: 'Sửa rồi', back: 'Mặt sau 1', citations: ['chunk-1'] })
    expect(sent[14]).toEqual({ front: 'Câu hỏi mới', back: 'Trả lời mới', citations: [] })
    expect(await screen.findByText('Đã lưu.')).toBeInTheDocument()
  })

  it('grows each side to its text, so a long answer is read whole, not cut by the box', async () => {
    // Live run 2 at 390 px: the back kept 3 rows and its fourth line was cut in half.
    const scrollHeight = vi.spyOn(HTMLTextAreaElement.prototype, 'scrollHeight', 'get')
      .mockImplementation(function (this: HTMLTextAreaElement) { return this.value.length > 20 ? 140 : 40 })
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1, { back: 'Một câu trả lời dài hơn ba dòng của ô.' })]))
    renderPage()

    const back = await screen.findByLabelText('Mặt sau thẻ 1')
    expect(back.style.height).toBe('140px')
    await userEvent.clear(back)
    await userEvent.type(back, 'Ngắn')
    expect(back.style.height).toBe('40px')
    scrollHeight.mockRestore()
  })

  it('does not save a card with an empty side', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Thêm thẻ' }))

    expect(screen.getByRole('button', { name: 'Lưu thay đổi' })).toBeDisabled()
    expect(screen.getByText('Mỗi thẻ cần cả mặt trước và mặt sau.')).toBeInTheDocument()
  })

  it('removes a card', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1), card(2)]))
    vi.mocked(api.saveFlashcards).mockResolvedValue(draft([card(2)]))

    renderPage()
    await userEvent.click((await screen.findAllByRole('button', { name: /Xóa thẻ/ }))[0])
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    expect(vi.mocked(api.saveFlashcards).mock.calls[0][2]).toEqual([
      { front: 'Mặt trước 2', back: 'Mặt sau 2', citations: ['chunk-1'] },
    ])
  })

  it('publishes only after saving', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.saveFlashcards).mockResolvedValue(draft([card(1, { front: 'Đã sửa', author: 'lecturer' })]))
    vi.mocked(api.publishFlashcardSet).mockResolvedValue(draft([card(1)], { published_at: '2026-01-02T00:00:00Z' }))

    renderPage()
    const publish = await screen.findByRole('button', { name: 'Giao cho sinh viên' })
    expect(publish).toBeEnabled()

    await userEvent.type(screen.getByLabelText('Mặt trước thẻ 1'), '!')
    expect(screen.getByRole('button', { name: 'Giao cho sinh viên' })).toBeDisabled()
    expect(screen.getByText('Lưu thay đổi trước khi giao.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Giao cho sinh viên' }))

    expect(api.publishFlashcardSet).toHaveBeenCalledWith('set-1', true)
    expect(await screen.findByRole('button', { name: 'Ngừng giao' })).toBeInTheDocument()
  })

  it('cannot publish an empty set', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([]))

    renderPage()

    expect(await screen.findByRole('button', { name: 'Giao cho sinh viên' })).toBeDisabled()
  })

  it('stops publishing a published set', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { published_at: '2026-01-02T00:00:00Z' }))
    vi.mocked(api.publishFlashcardSet).mockResolvedValue(draft([card(1)]))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Ngừng giao' }))

    expect(api.publishFlashcardSet).toHaveBeenCalledWith('set-1', false)
    expect(await screen.findByRole('button', { name: 'Giao cho sinh viên' })).toBeInTheDocument()
  })

  it('says what the server said when saving fails and keeps the edits', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.saveFlashcards).mockRejectedValue(axiosFailure(422, 'Trích nguồn phải là đoạn của chính tài liệu này.'))

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    expect(await screen.findByText('Trích nguồn phải là đoạn của chính tài liệu này.')).toBeInTheDocument()
    expect(screen.getByLabelText('Mặt trước thẻ 1')).toHaveValue('Mặt trước 1!')
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

    expect(await screen.findByLabelText('Mặt trước thẻ 1')).toHaveValue('Mặt trước 1')
  })

  it('says a failed generation failed', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([], { status: 'failed' }))

    renderPage()

    expect(await screen.findByText(/Tạo flashcard thất bại/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thêm thẻ' })).not.toBeInTheDocument()
  })

  it('says the set is missing on a 404', async () => {
    vi.mocked(api.getFlashcardSet).mockRejectedValueOnce(axiosFailure(404, 'nope'))
    renderPage()
    expect(await screen.findByText('Không tìm thấy bộ flashcard.')).toBeInTheDocument()
  })

  it('does not call a failed load a missing set', async () => {
    vi.mocked(api.getFlashcardSet)
      .mockRejectedValueOnce(axiosFailure(500, 'boom'))
      .mockResolvedValue(draft([card(1)]))

    renderPage()
    expect(await screen.findByText('Không tải được bộ flashcard.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }))

    expect(await screen.findByLabelText('Mặt trước thẻ 1')).toBeInTheDocument()
  })

  it('deletes the set after confirmation', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.deleteFlashcardSet).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    const del = await screen.findByRole('button', { name: 'Xóa bộ flashcard' })
    await userEvent.click(del)
    expect(api.deleteFlashcardSet).not.toHaveBeenCalled()
    await userEvent.click(del)

    expect(api.deleteFlashcardSet).toHaveBeenCalledWith('set-1')
    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
  })

  it('cites a passage found in the document for a card without a source', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1, { citations: [], sources: [] })]))
    vi.mocked(api.listDocumentChunks).mockResolvedValue({
      items: [{ chunk_id: 'chunk-9', locator: 'bai-giang.pdf, trang 9', content: 'Danh sách liên kết nối các nút bằng con trỏ.' }],
      total: 1,
    })
    vi.mocked(api.saveFlashcards).mockResolvedValue(draft([card(1)]))

    renderPage()
    const item = (await screen.findAllByRole('listitem'))[0]
    expect(within(item).getByText('Chưa có nguồn')).toBeInTheDocument()
    await userEvent.click(within(item).getByRole('button', { name: 'Tìm' }))

    expect(api.listDocumentChunks).toHaveBeenCalledWith('doc-1', { q: '', offset: 0 })
    await userEvent.click(await within(item).findByRole('checkbox', { name: /con trỏ/ }))
    expect(within(item).queryByText('Chưa có nguồn')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }))

    await waitFor(() => expect(api.saveFlashcards).toHaveBeenCalled())
    expect(vi.mocked(api.saveFlashcards).mock.calls[0][2][0].citations).toEqual(['chunk-9'])
  })

  it('shows where each card comes from as a numbered marker that opens its passage', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1), card(2, { citations: [], sources: [] })]))

    renderPage()

    const sources = await screen.findByRole('group', { name: 'Nguồn của thẻ 1' })
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
  })

  it('names a card source once, not again in the picker summary', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))

    renderPage()

    expect(await screen.findByRole('group', { name: 'Nguồn của thẻ 1' })).toBeInTheDocument()
    expect(screen.queryByText(/^Nguồn: /)).not.toBeInTheDocument()
    expect(screen.getByText('Đổi nguồn')).toBeInTheDocument()
  })

  it('marks as the card sources only the passages still ticked', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))

    renderPage()
    expect(await screen.findByRole('group', { name: 'Nguồn của thẻ 1' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /bai-giang.pdf, trang 3/ }))

    expect(screen.queryByRole('group', { name: 'Nguồn của thẻ 1' })).not.toBeInTheDocument()
    expect(screen.getByText('Gắn nguồn')).toBeInTheDocument()
  })

  it('goes back to its course, and says so while still generating', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([], { status: 'generating' }))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Quay lại' }))

    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
  })

  it('asks before leaving with unsaved edits, and stays when the lecturer says no', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Quay lại' }))
    expect(screen.queryByText('Trang môn học')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Quay lại' }))

    expect(confirm).toHaveBeenCalledTimes(2)
    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
  })

  it('asks before the sidebar leaves unsaved edits, and stays when the lecturer says no', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
    const sidebar = within(screen.getByRole('navigation', { name: 'Điều hướng chính' }))
    await userEvent.click(sidebar.getByRole('link', { name: 'Môn học' }))
    expect(confirm).toHaveBeenCalledWith('Các thay đổi chưa lưu sẽ mất. Rời trang?')
    expect(screen.queryByText('Danh sách môn học')).not.toBeInTheDocument()
    expect((screen.getByLabelText('Mặt trước thẻ 1') as HTMLTextAreaElement).value).toContain('!')

    await userEvent.click(sidebar.getByRole('link', { name: 'Môn học' }))
    expect(await screen.findByText('Danh sách môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('asks before the browser Back button leaves unsaved edits', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    const { router } = renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
    await act(() => router.navigate(-1))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Mặt trước thẻ 1')).toBeInTheDocument()

    await act(() => router.navigate(-1))
    expect(await screen.findByText('Danh sách môn học')).toBeInTheDocument()
  })

  it('asks before logging out with unsaved edits, once, and logs out when told to', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
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

  it('leaves for the course page without asking once it is deleted, unsaved edits and all', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    vi.mocked(api.deleteFlashcardSet).mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Xóa bộ flashcard' }))

    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(1) // the delete's own question only
  })

  it('leaves without asking when nothing is unsaved', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const confirm = vi.spyOn(window, 'confirm')

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Quay lại' }))

    expect(confirm).not.toHaveBeenCalled()
    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
  })

  it('warns the browser about unsaved edits only while there are some', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))

    renderPage()
    const field = await screen.findByLabelText('Mặt trước thẻ 1')
    const closing = () => {
      const event = new Event('beforeunload', { cancelable: true })
      window.dispatchEvent(event)
      return event.defaultPrevented
    }
    expect(closing()).toBe(false)
    await userEvent.type(field, '!')
    expect(closing()).toBe(true)
  })

  it('tells the lecturer that a published set needs saving to reach the learners', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { published_at: '2026-01-02T00:00:00Z' }))

    renderPage()
    await userEvent.type(await screen.findByLabelText('Mặt trước thẻ 1'), '!')

    expect(screen.getByText('Lưu để cập nhật cho sinh viên.')).toBeInTheDocument()
  })

  it('shortens a long focus in the title and keeps the whole text on hover', async () => {
    const focus = 'Ví dụ: các định nghĩa trong chương 2 '.repeat(6).trim()
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { focus }))

    renderPage()

    const heading = await screen.findByRole('heading', { level: 1 })
    expect(heading.textContent!.length).toBeLessThanOrEqual(60)
    expect(heading.textContent).toMatch(/…$/)
    expect(heading).toHaveAttribute('title', `Flashcard: ${focus}`)
  })

  it('shows a draft as neutral and a published set as done', async () => {
    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)]))
    const first = renderPage()
    expect(await screen.findByText('Bản nháp')).not.toHaveAttribute('data-status')
    first.unmount()

    vi.mocked(api.getFlashcardSet).mockResolvedValue(draft([card(1)], { published_at: '2026-01-02T00:00:00Z' }))
    renderPage()
    expect(await screen.findByText('Đã giao cho sinh viên')).toHaveAttribute('data-status', 'embedded')
  })
})
