import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModulePreviewPage } from './ModulePreviewPage'
import { AuthProvider } from '../auth/AuthContext'
import * as api from '../api'
import type { ModuleDetail } from '../api'

// Keep the client and error handling real; replace the other server calls.
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { chatEditModule: vi.fn(), ...Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['api', 'apiErrorMessage'].includes(name) ? vi.fn() : value,
    ]),
  ) }
})

const baseModule: ModuleDetail = {
  is_control: false,
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

it('shows citation labels and reveals the original chunk only when opened', async () => {
  const citations = [
    { claim: 'A grounded claim', locator: 'lesson.pdf · trang 9–10 · Search', chunk_content: 'Original source paragraph', resolution: 'resolved' as const },
    { claim: 'Missing claim', locator: 'lesson.pdf, trang 9', chunk_content: null, resolution: 'missing' as const },
    { claim: 'Ambiguous claim', locator: 'lesson.pdf, trang 3', chunk_content: null, resolution: 'ambiguous' as const },
  ]
  const module = {
    ...baseModule,
    claim_citations: citations,
    components: [{ id: 'content-1', type: 'content' as const, content: { markdown: 'Lesson' }, questions: [] }],
  }
  vi.mocked(api.getModule).mockResolvedValue(module)
  const page = renderPage()
  expect(await screen.findByText('A grounded claim')).toBeInTheDocument()
  const label = screen.getByText('lesson.pdf · trang 9–10 · Search')
  const details = label.closest('details')!
  expect(details).not.toHaveAttribute('open')
  await userEvent.click(label)
  expect(details).toHaveAttribute('open')
  expect(within(details).getByText('Original source paragraph')).toBeVisible()
  expect(screen.getAllByText('không tìm thấy đoạn nguồn')).toHaveLength(1)
  expect(screen.getByText('trùng nhiều đoạn nguồn, không xác định được')).toBeInTheDocument()
  page.unmount()
  vi.mocked(api.getModule).mockResolvedValue({ ...module, claim_citations: null })
  renderPage()
  await screen.findByText('Lesson')
  expect(screen.queryByText('A grounded claim')).not.toBeInTheDocument()
})

it('downloads slides through the authenticated API client', async () => {
  vi.mocked(api.getModule).mockResolvedValue({
    ...baseModule,
    components: [{ id: 'slides-1', type: 'slides', content: { slides: [] }, questions: [] }],
  })
  const blob = new Blob(['pptx'])
  const request = vi.spyOn(api.api, 'get').mockResolvedValue({ data: blob })
  const createURL = vi.fn().mockReturnValue('blob:slides')
  const revokeURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL: createURL, revokeObjectURL: revokeURL })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.href).toBe('blob:slides')
    expect(this.download).toBe('Test Module.pptx')
  })
  try {
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Tải slide' }))
    await waitFor(() => expect(click).toHaveBeenCalledOnce())
    expect(request).toHaveBeenCalledWith('/modules/module-1/slides.pptx', { responseType: 'blob' })
    expect(createURL).toHaveBeenCalledWith(blob)
    expect(revokeURL).toHaveBeenCalledWith('blob:slides')
  } finally {
    request.mockRestore()
    click.mockRestore()
    vi.unstubAllGlobals()
  }
})

describe('ModulePreviewPage - publish to Moodle', () => {
  it('labels a control module and hides publishing even when the course is linked', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, is_control: true, moodle_course_id: 6 })
    vi.mocked(api.getMoodleSections).mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('Đối chứng (không RAG) - chỉ dùng cho nghiên cứu')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push to moodle/i })).not.toBeInTheDocument()
    expect(api.publishToMoodle).not.toHaveBeenCalled()
  })

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

  it('says a question was not self-checked instead of claiming it passed the CRAG check', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      components: [
        {
          id: 'quiz-1',
          type: 'quiz',
          content: { self_checked: false, questions: [{ passed: true }] },
          questions: [
            { id: 'q1', position: 0, stem: 'CÂU-HỎI', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'e' },
          ],
        },
      ],
    })

    renderPage()

    const quizCard = (await screen.findByText('CÂU-HỎI')).closest('section') as HTMLElement
    expect(within(quizCard).getByText('Chưa tự kiểm')).toBeInTheDocument()
    expect(within(quizCard).queryByText('Đạt CRAG groundedness/answerability')).not.toBeInTheDocument()
  })

  it('treats a module saved before the self-check flag existed as self-checked', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      components: [
        {
          id: 'quiz-1',
          type: 'quiz',
          content: { questions: [{ passed: true }] },
          questions: [
            { id: 'q1', position: 0, stem: 'CÂU-HỎI', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'e' },
          ],
        },
      ],
    })

    renderPage()

    const quizCard = (await screen.findByText('CÂU-HỎI')).closest('section') as HTMLElement
    expect(within(quizCard).getByText('Đạt CRAG groundedness/answerability')).toBeInTheDocument()
    expect(within(quizCard).queryByText('Chưa tự kiểm')).not.toBeInTheDocument()
  })

  it('lists the sub-objectives under the learning objective', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, sub_objectives: ['MỤC-TIÊU-CON-A', 'MỤC-TIÊU-CON-B'] })

    renderPage()

    expect(await screen.findByText('MỤC-TIÊU-CON-A')).toBeInTheDocument()
    expect(screen.getByText('MỤC-TIÊU-CON-B')).toBeInTheDocument()
    expect(screen.getByText('Understand X')).toBeInTheDocument()
    expect(screen.queryByText(/Bloom:/i)).not.toBeInTheDocument()
  })
})

it.each(['quiz', 'practice'] as const)('edits and saves a %s question', async (type) => {
  const question = { id: 'q1', position: 0, stem: 'Original stem', choices: { options: ['A', 'B'] }, answer_key: '0', explanation: 'Original explanation' }
  const module = { ...baseModule, components: [{ id: 'quiz-1', type, content: {}, questions: [question] }] }
  vi.mocked(api.getModule).mockResolvedValue(module)
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Sửa' }))
  const stem = screen.getByRole('textbox', { name: 'Câu hỏi' })
  await user.clear(stem)
  await user.type(stem, 'Edited stem')
  const choice = screen.getByRole('textbox', { name: 'Phương án B' })
  await user.clear(choice)
  await user.type(choice, 'Edited B')
  await user.click(screen.getByRole('radio', { name: 'Đáp án B' }))
  const explanation = screen.getByRole('textbox', { name: 'Giải thích' })
  await user.clear(explanation)
  await user.type(explanation, 'Edited explanation')
  vi.mocked(api.getModule).mockResolvedValue({ ...module, components: [{ ...module.components[0], questions: [{ ...question, stem: 'Edited stem', choices: { options: ['A', 'Edited B'] }, answer_key: '1', explanation: 'Edited explanation' }] }] })
  await user.click(screen.getByRole('button', { name: 'Lưu' }))
  await waitFor(() => expect(api.updateQuestion).toHaveBeenCalledWith('module-1', 'q1', { stem: 'Edited stem', choices: ['A', 'Edited B'], answer_key: 1, explanation: 'Edited explanation' }))
  expect(await screen.findByText('Edited stem')).toBeInTheDocument()
})

it('falls back to the question explanation when its Explanation item is empty', async () => {
  const question = { id: 'q1', position: 0, stem: 'Original stem', choices: { options: ['A', 'B'] }, answer_key: '0', explanation: 'Question explanation' }
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, components: [
    { id: 'quiz-1', type: 'quiz', content: {}, questions: [question] },
    { id: 'exp-1', type: 'explanation', content: { items: [{ component: 'quiz', position: 0, reasoning: 'r', explanation_text: '' }] }, questions: [] },
  ] })
  const user = userEvent.setup()
  renderPage()
  expect(await screen.findByText('Question explanation')).toBeInTheDocument()
  await user.click(screen.getAllByRole('button', { name: 'Sửa' })[0])
  const stem = screen.getByRole('textbox', { name: 'Câu hỏi' })
  await user.clear(stem)
  await user.type(stem, 'Edited stem')
  await user.click(screen.getByRole('button', { name: 'Lưu' }))
  await waitFor(() => expect(api.updateQuestion).toHaveBeenCalledWith('module-1', 'q1', { stem: 'Edited stem', choices: ['A', 'B'], answer_key: 0, explanation: 'Question explanation' }))
})

it('shows the Vietnamese deleted-course error and offers relinking immediately', async () => {
  const message = 'Khóa học trên Moodle đã bị xóa hoặc không còn tồn tại. Hãy liên kết lại hoặc tạo khóa học Moodle mới.'
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6 })
  vi.mocked(api.getMoodleSections).mockResolvedValue([])
  vi.mocked(api.publishToMoodle).mockRejectedValue({ isAxiosError: true, response: { data: { detail: message } } })
  renderPage()
  const button = await screen.findByRole('button', { name: /push to moodle/i })
  vi.mocked(api.getModule).mockResolvedValue(baseModule)
  await userEvent.click(button)
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: /vào trang course để liên kết trước/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /push to moodle/i })).not.toBeInTheDocument()
})

it('refreshes the preview after an applied chat edit and shows the reply', async () => {
  vi.mocked(api.getModule).mockResolvedValueOnce(baseModule).mockResolvedValue({ ...baseModule, title: 'Updated module' })
  vi.mocked(api.chatEditModule).mockResolvedValue({ session_id: 'session-1', applied: true, reply: 'Đã sửa slide 1.', edit_count: 1 })
  renderPage()
  await userEvent.type(await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' }), 'Rút gọn slide 1')
  await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
  expect(await screen.findByText('Đã sửa slide 1.')).toBeInTheDocument()
  expect(await screen.findByText('Updated module')).toBeInTheDocument()
})

it('shows a clarification reply without refreshing the preview', async () => {
  vi.mocked(api.getModule).mockResolvedValue(baseModule)
  vi.mocked(api.chatEditModule).mockResolvedValue({ session_id: 'session-2', applied: false, reply: 'Bạn muốn sửa phần nào?', edit_count: 0 })
  renderPage()
  await userEvent.type(await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' }), 'Sửa lại')
  await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
  expect(await screen.findByText('Bạn muốn sửa phần nào?')).toBeInTheDocument()
  expect(api.getModule).toHaveBeenCalledTimes(1)
})

it('asks for a reload instead of a retry when the refresh after an applied chat edit fails', async () => {
  vi.mocked(api.getModule).mockResolvedValueOnce(baseModule).mockRejectedValue(new Error('offline'))
  vi.mocked(api.chatEditModule).mockResolvedValue({ session_id: 'session-3', applied: true, reply: 'Đã sửa slide 1.', edit_count: 1 })
  renderPage()
  await userEvent.type(await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' }), 'Rút gọn slide 1')
  await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
  expect(await screen.findByText('Đã áp dụng chỉnh sửa nhưng không tải lại được mô-đun. Vui lòng tải lại trang.')).toBeInTheDocument()
  expect(screen.queryByText('Chỉnh sửa thất bại. Vui lòng thử lại.')).not.toBeInTheDocument()
})

it('hides the chat edit panel for research control modules', async () => {
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, is_control: true })
  renderPage()
  expect(await screen.findByText('Đối chứng (không RAG) - chỉ dùng cho nghiên cứu')).toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })).not.toBeInTheDocument()
})

it('opens the editor on the chat-edited content instead of the stale draft', async () => {
  const withContent = (markdown: string) => ({ ...baseModule, components: [
    { id: 'content-1', type: 'content' as const, content: { markdown }, questions: [] },
  ] })
  vi.mocked(api.getModule).mockResolvedValueOnce(withContent('Bản gốc dài')).mockResolvedValue(withContent('Bản rút gọn'))
  vi.mocked(api.chatEditModule).mockResolvedValue({ session_id: 'session-4', applied: true, reply: 'Đã rút gọn.', edit_count: 1 })
  renderPage()
  await userEvent.type(await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' }), 'Rút gọn nội dung')
  await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
  expect(await screen.findByText('Bản rút gọn')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Sửa' }))
  expect(screen.getByDisplayValue('Bản rút gọn')).toBeInTheDocument()
})

describe('ModulePreviewPage - forms', () => {
  const withComponent = (component: ModuleDetail['components'][number]): ModuleDetail => ({ ...baseModule, components: [component] })

  it('asks for a request instead of sending a blank chat message', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    renderPage()
    const field = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    await userEvent.type(field, '   ')
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu' })).toBeDisabled()
    fireEvent.submit(field.closest('form')!)
    expect(await screen.findByText('Vui lòng nhập yêu cầu chỉnh sửa')).toBeInTheDocument()
    expect(api.chatEditModule).not.toHaveBeenCalled()
  })

  it('sends the trimmed chat message with the session of the last turn and empties the field', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    vi.mocked(api.chatEditModule)
      .mockResolvedValueOnce({ session_id: 'session-1', applied: false, reply: 'Phần nào?', edit_count: 0 })
      .mockResolvedValueOnce({ session_id: 'session-1', applied: false, reply: 'Rõ rồi.', edit_count: 0 })
    renderPage()
    const field = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    await userEvent.type(field, '  Sửa lại ')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    await screen.findByText('Phần nào?')
    expect(api.chatEditModule).toHaveBeenLastCalledWith('module-1', 'Sửa lại', undefined)
    expect(field).toHaveValue('')
    await userEvent.type(field, 'Slide 3')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    await screen.findByText('Rõ rồi.')
    expect(api.chatEditModule).toHaveBeenLastCalledWith('module-1', 'Slide 3', 'session-1')
  })

  it('keeps the chat message for another try when the edit fails', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    vi.mocked(api.chatEditModule).mockRejectedValue(new Error('boom'))
    renderPage()
    const field = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    await userEvent.type(field, 'Sửa lại')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Chỉnh sửa thất bại. Vui lòng thử lại.')).toBeInTheDocument()
    expect(field).toHaveValue('Sửa lại')
  })

  it('refuses a chat message over 4000 characters', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    renderPage()
    const field = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    fireEvent.change(field, { target: { value: 'a'.repeat(4001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Yêu cầu tối đa 4000 ký tự')).toBeInTheDocument()
    expect(api.chatEditModule).not.toHaveBeenCalled()
  })

  it('renames the module and goes back to the heading', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    vi.mocked(api.updateModuleTitle).mockResolvedValue({ id: 'module-1', title: 'Tên mới' })
    renderPage()
    await userEvent.click(await screen.findByRole('heading', { name: 'Test Module' }))
    const field = screen.getByRole('textbox', { name: 'Tên mô-đun' })
    expect(field).toHaveValue('Test Module')
    await userEvent.clear(field)
    await userEvent.type(field, 'Tên mới')
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, title: 'Tên mới' })
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateModuleTitle).toHaveBeenCalledWith('module-1', 'Tên mới'))
    expect(await screen.findByRole('heading', { name: 'Tên mới' })).toBeInTheDocument()
  })

  it('refuses a module title over 200 characters and puts the old one back on cancel', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    renderPage()
    await userEvent.click(await screen.findByRole('heading', { name: 'Test Module' }))
    const field = screen.getByRole('textbox', { name: 'Tên mô-đun' })
    fireEvent.change(field, { target: { value: 'a'.repeat(201) } })
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText('Tên mô-đun tối đa 200 ký tự')).toBeInTheDocument()
    expect(api.updateModuleTitle).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await userEvent.click(screen.getByRole('heading', { name: 'Test Module' }))
    expect(screen.getByRole('textbox', { name: 'Tên mô-đun' })).toHaveValue('Test Module')
  })

  it('saves edited Markdown content and keeps the rest of the component content', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({ id: 'content-1', type: 'content', content: { markdown: 'Bản gốc', extra: 1 }, questions: [] }))
    vi.mocked(api.updateComponentContent).mockResolvedValue({ id: 'content-1', content: {} })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    const field = screen.getByRole('textbox', { name: 'Nội dung' })
    expect(field).toHaveValue('Bản gốc')
    await userEvent.clear(field)
    await userEvent.type(field, 'Bản sửa')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalledWith('module-1', 'content-1', { markdown: 'Bản sửa', extra: 1 }))
  })

  it('discards an edit of the Markdown content on cancel', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({ id: 'content-1', type: 'content', content: { markdown: 'Bản gốc' }, questions: [] }))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Nội dung' }), ' thêm')
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    expect(screen.getByRole('textbox', { name: 'Nội dung' })).toHaveValue('Bản gốc')
  })

  it('saves edited slides, a line to a bullet, and keeps what else a slide holds', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({
      id: 'slides-1',
      type: 'slides',
      content: { slides: [{ title: 'Một', bullets: ['a', 'b'], notes: 'n' }, { title: 'Hai', bullets: ['c'] }] },
      questions: [],
    }))
    vi.mocked(api.updateComponentContent).mockResolvedValue({ id: 'slides-1', content: {} })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    const title = screen.getByRole('textbox', { name: 'Tiêu đề slide 1' })
    await userEvent.clear(title)
    await userEvent.type(title, 'Một mới')
    const bullets = screen.getByRole('textbox', { name: 'Nội dung slide 1' })
    expect(bullets).toHaveValue('a\nb')
    await userEvent.type(bullets, '{Enter}c')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalledWith('module-1', 'slides-1', {
      slides: [{ title: 'Một mới', bullets: ['a', 'b', 'c'], notes: 'n' }, { title: 'Hai', bullets: ['c'] }],
    }))
  })

  it('saves an edited example and keeps the others', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({
      id: 'example-1',
      type: 'example',
      content: { examples: [{ context: 'C1', analysis: 'A1' }, { context: 'C2', analysis: 'A2' }] },
      questions: [],
    }))
    vi.mocked(api.updateComponentContent).mockResolvedValue({ id: 'example-1', content: {} })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    const field = screen.getByLabelText('Phân tích ví dụ 2')
    await userEvent.clear(field)
    await userEvent.type(field, 'A2 mới')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalledWith('module-1', 'example-1', {
      examples: [{ context: 'C1', analysis: 'A1' }, { context: 'C2', analysis: 'A2 mới' }],
    }))
  })

  describe('editing a question', () => {
    const question = { id: 'q1', position: 0, stem: 'Original stem', choices: { options: ['A', 'B'] }, answer_key: '0', explanation: 'Original explanation' }
    const quiz = withComponent({ id: 'quiz-1', type: 'quiz', content: {}, questions: [question] })

    it('asks for the question before saving it', async () => {
      vi.mocked(api.getModule).mockResolvedValue(quiz)
      renderPage()
      await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
      await userEvent.clear(screen.getByRole('textbox', { name: 'Câu hỏi' }))
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Vui lòng nhập câu hỏi')).toBeInTheDocument()
      expect(api.updateQuestion).not.toHaveBeenCalled()
    })

    it('asks for every choice before saving the question', async () => {
      vi.mocked(api.getModule).mockResolvedValue(quiz)
      renderPage()
      await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
      await userEvent.clear(screen.getByRole('textbox', { name: 'Phương án B' }))
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Vui lòng nhập phương án')).toBeInTheDocument()
      expect(api.updateQuestion).not.toHaveBeenCalled()
    })

    it('starts the editor on the saved answer and says when the save fails', async () => {
      vi.mocked(api.getModule).mockResolvedValue({ ...quiz, components: [{ ...quiz.components[0], questions: [{ ...question, answer_key: '1' }] }] })
      vi.mocked(api.updateQuestion).mockRejectedValue(new Error('boom'))
      renderPage()
      await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
      expect(screen.getByRole('radio', { name: 'Đáp án B' })).toBeChecked()
      expect(screen.getByRole('radio', { name: 'Đáp án A' })).not.toBeChecked()
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Lưu câu hỏi thất bại')).toBeInTheDocument()
      expect(api.updateQuestion).toHaveBeenCalledWith('module-1', 'q1', expect.objectContaining({ answer_key: 1 }))
    })
  })

  describe('publishing to Moodle', () => {
    const linked = { ...baseModule, moodle_course_id: 6 }
    const sections = [{ sectionnum: 0, name: 'General' }, { sectionnum: 1, name: 'Tuần 1' }, { sectionnum: 2, name: 'Tuần 2' }]
    const result = { moodle_course_id: 6, book_cmid: 1 }

    it('pushes to the section picked from the drop-down', async () => {
      vi.mocked(api.getModule).mockResolvedValue(linked)
      vi.mocked(api.getMoodleSections).mockResolvedValue(sections)
      vi.mocked(api.publishToMoodle).mockResolvedValue(result)
      renderPage()
      await userEvent.selectOptions(await screen.findByRole('combobox'), '2')
      await userEvent.click(screen.getByRole('button', { name: /push to moodle/i }))
      await waitFor(() => expect(api.publishToMoodle).toHaveBeenCalledWith('module-1', 2))
    })

    it('pushes to a new section, numbered after the existing ones, and hides the drop-down', async () => {
      vi.mocked(api.getModule).mockResolvedValue(linked)
      vi.mocked(api.getMoodleSections).mockResolvedValue(sections)
      vi.mocked(api.publishToMoodle).mockResolvedValue(result)
      renderPage()
      await screen.findByRole('combobox')
      await userEvent.click(screen.getByRole('checkbox', { name: /thêm vào 1 section mới/i }))
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /push to moodle/i }))
      await waitFor(() => expect(api.publishToMoodle).toHaveBeenCalledWith('module-1', 3))
    })
  })
})
