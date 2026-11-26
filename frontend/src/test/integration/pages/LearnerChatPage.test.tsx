import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../../api'
import * as AuthHookModule from '../../../auth/useAuth'

vi.mock('../../../components/CourseChat', () => ({
  CourseChat: ({ courseId }: { courseId: string }) => <p>chat of {courseId}</p>,
}))

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return { ...actual, listLearnItems: vi.fn() }
})

const { LearnerChatPage } = await import('../../../pages/LearnerChatPage')

function renderAt(path: string) {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r',
    user: { id: 'u', email: 'e', name: 'Sinh Viên A', is_admin: false, can_author: false },
    login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
  })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/learn" element={<LearnerChatPage />} />
        <Route path="/learn/:courseId" element={<LearnerChatPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const doc = (id: string, filename: string) => ({ id, filename })
const set = (id: string, focus: string | null, day: string) =>
  ({ id, focus, card_count: 8, published_at: `2026-11-${day}T08:00:00Z`, document: doc('d1', 'Chapter 5.pdf') })

beforeEach(() => {
  vi.mocked(api.listLearnItems).mockResolvedValue({ flashcard_sets: [], videos: [] })
})

describe('LearnerChatPage', () => {
  it('lists the published sets and videos by document, above the chat', async () => {
    vi.mocked(api.listLearnItems).mockResolvedValue({
      flashcard_sets: [
        { id: 's1', focus: 'Mảng', card_count: 8, published_at: '2026-11-16T03:00:00', document: doc('d1', 'bai-1.pdf') },
        { id: 's2', focus: null, card_count: 1, published_at: '2026-11-15T03:00:00', document: doc('d2', 'bai-2.pdf') },
      ],
      videos: [{ id: 'v1', focus: 'Giới thiệu', duration_seconds: 125, published_at: '2026-11-14T03:00:00', document: doc('d1', 'bai-1.pdf') }],
    })

    renderAt('/learn/course-1')

    expect(await screen.findByRole('heading', { name: 'bai-1.pdf' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'bai-2.pdf' })).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Mảng/ })
    expect(link).toHaveAttribute('href', '/learn/course-1/flashcards/s1')
    expect(link).toHaveTextContent(/8 thẻ\s*Giao 16\/11/)
    expect(screen.getByRole('link', { name: 'Bộ thẻ, 1 thẻ, giao 15/11' })).toHaveAttribute('href', '/learn/course-1/flashcards/s2')
    expect(screen.getByText(/Giới thiệu/).closest('li')).toHaveTextContent(/2:05\s*Giao 14\/11/)
    expect(screen.getByRole('link', { name: /Giới thiệu/ })).toHaveAttribute('href', '/learn/course-1/videos/v1')
    const chat = screen.getByText('chat of course-1')
    expect(screen.getByRole('heading', { name: 'bai-2.pdf' }).compareDocumentPosition(chat))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('leads with the newest delivery of each focus and folds the earlier ones under it', async () => {
    vi.mocked(api.listLearnItems).mockResolvedValue({
      flashcard_sets: [
        set('a', 'FCFS và Round Robin', '13'),
        set('b', 'FCFS và Round Robin ', '16'),
        set('c', 'fcfs và round robin', '14'),
        set('d', 'Deadlock', '12'),
      ],
      videos: [],
    } as never)

    renderAt('/learn/c1')

    const section = await screen.findByRole('region', { name: 'Bộ thẻ và video' })
    const list = within(section).getAllByRole('list')[0]
    const rows = within(list).getAllByRole('listitem').filter((li) => li.parentElement === list)
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getAllByRole('link')[0]).toHaveAttribute('href', '/learn/c1/flashcards/b')
    expect(within(rows[0]).getByText('2 bản giao trước')).toBeInTheDocument()
    const earlier = within(rows[0]).getAllByRole('link', { hidden: true }).slice(1).map((a) => a.getAttribute('href'))
    expect(earlier).toEqual(['/learn/c1/flashcards/c', '/learn/c1/flashcards/a'])
    expect(within(rows[1]).queryByText(/bản giao trước/)).not.toBeInTheDocument()
  })

  it('says nothing is published yet, and still shows the chat and learner name', async () => {
    renderAt('/learn/course-1')

    expect(await screen.findByText('Giảng viên chưa giao bộ thẻ hay video nào.')).toBeInTheDocument()
    expect(screen.getByText('chat of course-1')).toBeInTheDocument()
    expect(screen.getByText('Sinh Viên A')).toBeInTheDocument()
  })

  it('keeps the chat when the list cannot be loaded', async () => {
    vi.mocked(api.listLearnItems).mockRejectedValue(new Error('x'))

    renderAt('/learn/course-1')

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được danh sách')
    expect(screen.getByText('chat of course-1')).toBeInTheDocument()
  })

  it('asks to open the tool from the LMS without a course', () => {
    renderAt('/learn')
    expect(screen.getByRole('status')).toHaveTextContent('Moodle')
  })
})
