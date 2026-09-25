import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModulePreviewPage } from './ModulePreviewPage'
import { AuthProvider } from '../auth/AuthContext'
import * as api from '../api'
import type { ModuleDetail } from '../api'

// Every function of the module is replaced except the one that words an error: that is
// real logic the pages rely on, not a call to the server.
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

const baseModule: ModuleDetail = {
  id: 'module-1',
  title: 'Test Module',
  learning_objective: 'Understand X',
  bloom_level: 'understand',
  course_id: 'course-1',
  moodle_course_id: null,
  components: [],
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/modules/module-1']}>
        <Routes>
          <Route path="/modules/:moduleId" element={<ModulePreviewPage />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('ModulePreviewPage - publish to Moodle', () => {
  it('prompts to link the course first when it has no moodle_course_id', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)

    renderPage()

    expect(await screen.findByText(/chưa được liên kết với moodle/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push to moodle/i })).not.toBeInTheDocument()
  })

  it('disables the push button after a successful publish so it cannot be clicked twice', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6 })
    vi.mocked(api.getMoodleSections).mockResolvedValue([{ sectionnum: 0, name: 'General' }])
    vi.mocked(api.publishToMoodle).mockResolvedValue({
      moodle_course_id: 6,
      book_cmid: 42,
      practice_cmid: 43,
      quiz_cmid: 44,
    })

    renderPage()

    const button = await screen.findByRole('button', { name: /push to moodle/i })
    expect(button).toBeEnabled()

    await userEvent.click(button)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /đã đẩy lên moodle/i })).toBeDisabled()
    })
    expect(api.publishToMoodle).toHaveBeenCalledTimes(1)
    // Says what was created, in the order it was created.
    expect(screen.getByText(/sách nội dung, practice, quiz đã tạo thành công/i)).toBeInTheDocument()

    // Clicking again (e.g. a stray extra click before React re-renders)
    // must not fire a second request -- moodle_client.create_book/create_quiz
    // are not idempotent, a second call would create duplicate content.
    await userEvent.click(screen.getByRole('button', { name: /đã đẩy lên moodle/i }))
    expect(api.publishToMoodle).toHaveBeenCalledTimes(1)
  })

  it('shows an error and keeps the button enabled when publishing fails', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6 })
    vi.mocked(api.getMoodleSections).mockResolvedValue([])
    vi.mocked(api.publishToMoodle).mockRejectedValue(new Error('boom'))

    renderPage()

    const button = await screen.findByRole('button', { name: /push to moodle/i })
    await userEvent.click(button)

    expect(await screen.findByText(/push to moodle thất bại/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /push to moodle/i })).toBeEnabled()
  })
})

describe('ModulePreviewPage - module structure', () => {
  const structured: ModuleDetail = {
    ...baseModule,
    components: [
      {
        id: 'example-1',
        type: 'example',
        content: {
          examples: [
            { context: 'BỐI-CẢNH-MỘT', analysis: 'PHÂN-TÍCH-MỘT' },
            { context: 'BỐI-CẢNH-HAI', analysis: 'PHÂN-TÍCH-HAI' },
          ],
        },
        questions: [],
      },
      {
        id: 'quiz-1',
        type: 'quiz',
        content: { questions: [{ passed: true }, { passed: false }] },
        // Out of order on purpose: the page must follow position, not the order they arrive in.
        questions: [
          { id: 'q2', position: 1, stem: 'CÂU-HỎI-HAI', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '2', explanation: 'GIẢI-THÍCH-HAI' },
          { id: 'q1', position: 0, stem: 'CÂU-HỎI-MỘT', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '1', explanation: 'GIẢI-THÍCH-MỘT' },
        ],
      },
      {
        id: 'explanation-1',
        type: 'explanation',
        content: {
          items: [
            { component: 'quiz', position: 0, reasoning: 'LẬP-LUẬN-MỘT', explanation_text: 'LỜI-GIẢI-MỘT' },
            { component: 'quiz', position: 1, reasoning: 'LẬP-LUẬN-HAI', explanation_text: 'LỜI-GIẢI-HAI' },
          ],
        },
        questions: [],
      },
    ],
  }

  it('shows every example of the module', async () => {
    vi.mocked(api.getModule).mockResolvedValue(structured)

    renderPage()

    for (const text of ['BỐI-CẢNH-MỘT', 'PHÂN-TÍCH-MỘT', 'BỐI-CẢNH-HAI', 'PHÂN-TÍCH-HAI']) {
      expect(await screen.findByText(text)).toBeInTheDocument()
    }
  })

  it('shows every quiz question in its order, each with its own explanation', async () => {
    vi.mocked(api.getModule).mockResolvedValue(structured)

    renderPage()

    const first = await screen.findByText('CÂU-HỎI-MỘT')
    const second = screen.getByText('CÂU-HỎI-HAI')
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const quizCard = first.closest('section') as HTMLElement
    const firstExplanation = within(quizCard).getByText('LỜI-GIẢI-MỘT')
    expect(first.compareDocumentPosition(firstExplanation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(firstExplanation.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('saves an edited explanation for the question it belongs to and keeps the others', async () => {
    vi.mocked(api.getModule).mockResolvedValue(structured)
    vi.mocked(api.updateComponentContent).mockResolvedValue({ id: 'explanation-1', content: {} })
    const user = userEvent.setup()

    renderPage()

    const explanationCard = (await screen.findByText('Giải thích đáp án')).closest('section') as HTMLElement
    await user.click(within(explanationCard).getByRole('button', { name: 'Sửa' }))
    const field = within(explanationCard).getByLabelText('Giải thích Quiz - câu 2')
    await user.clear(field)
    await user.type(field, 'LỜI-GIẢI-ĐÃ-SỬA')
    await user.click(within(explanationCard).getByRole('button', { name: 'Lưu' }))

    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalled())
    const [, componentId, content] = vi.mocked(api.updateComponentContent).mock.calls[0]
    expect(componentId).toBe('explanation-1')
    expect(content).toEqual({
      items: [
        { component: 'quiz', position: 0, reasoning: 'LẬP-LUẬN-MỘT', explanation_text: 'LỜI-GIẢI-MỘT' },
        { component: 'quiz', position: 1, reasoning: 'LẬP-LUẬN-HAI', explanation_text: 'LỜI-GIẢI-ĐÃ-SỬA' },
      ],
    })
  })
})

describe('ModulePreviewPage - reading the module', () => {
  it('renders the content as formatted Markdown, not as raw text', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      components: [
        { id: 'content-1', type: 'content', content: { markdown: '## TIÊU-ĐỀ-PHẦN\n- ý đầu tiên' }, questions: [] },
      ],
    })

    renderPage()

    expect(await screen.findByRole('heading', { name: 'TIÊU-ĐỀ-PHẦN' })).toBeInTheDocument()
    expect(screen.getByRole('listitem')).toHaveTextContent('ý đầu tiên')
    expect(screen.queryByText(/## TIÊU-ĐỀ-PHẦN/)).not.toBeInTheDocument()
  })

  it('shows under each question the explanation the teacher edited, which is the one Moodle gets', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      components: [
        {
          id: 'quiz-1',
          type: 'quiz',
          content: { questions: [{ passed: true }] },
          questions: [
            { id: 'q1', position: 0, stem: 'CÂU-HỎI', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'BẢN-LÚC-SINH' },
          ],
        },
        {
          id: 'explanation-1',
          type: 'explanation',
          content: { items: [{ component: 'quiz', position: 0, reasoning: 'r', explanation_text: 'BẢN-ĐÃ-SỬA' }] },
          questions: [],
        },
      ],
    })

    renderPage()

    const quizCard = (await screen.findByText('CÂU-HỎI')).closest('section') as HTMLElement
    expect(within(quizCard).getByText('BẢN-ĐÃ-SỬA')).toBeInTheDocument()
    expect(within(quizCard).queryByText('BẢN-LÚC-SINH')).not.toBeInTheDocument()
  })

  it('lists the sub-objectives under the learning objective', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, sub_objectives: ['MỤC-TIÊU-CON-A', 'MỤC-TIÊU-CON-B'] })

    renderPage()

    expect(await screen.findByText('MỤC-TIÊU-CON-A')).toBeInTheDocument()
    expect(screen.getByText('MỤC-TIÊU-CON-B')).toBeInTheDocument()
  })
})
