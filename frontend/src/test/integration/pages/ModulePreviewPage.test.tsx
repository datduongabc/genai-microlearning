import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ModulePreviewPage } from '../../../pages/ModulePreviewPage'
import { AuthProvider } from '../../../auth/AuthContext'
import * as api from '../../../api'
import type { ModuleDetail } from '../../../api'

// Keep the client and error handling real; replace the other server calls.
vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return { chatEditModule: vi.fn(), ...Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['api', 'apiErrorMessage', 'blobErrorMessage'].includes(name) ? vi.fn() : value,
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
  vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'teacher@example.com', status: 'linked' })
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

it('exports PPTX and SCORM, shows SCORM 422 error, prompts unlinked Moodle, and hides generation_models', async () => {
  vi.mocked(api.getModule).mockResolvedValue({
    ...baseModule,
    generation_models: [{ requested_alias: 'gemini-flash', served_model: 'google/gemini-3.8-flash' }],
    components: [{ id: 'slides-1', type: 'slides', content: { slides: [] }, questions: [] }],
  })
  const pptxBlob = new Blob(['pptx'])
  const zipBlob = new Blob(['zip'])
  const errorBody = new Blob([JSON.stringify({ detail: 'Còn 1 câu hỏi cần xem lại (quiz 1). Lý do ghi dưới từng câu; hãy sửa trước khi xuất gói SCORM.' })], { type: 'application/json' })
  const refused = new AxiosError('422', 'ERR_BAD_REQUEST', undefined, undefined, {
    status: 422, data: errorBody, statusText: '', headers: {}, config: {} as InternalAxiosRequestConfig,
  } as AxiosResponse)
  let scormAttempts = 0
  const request = vi.spyOn(api.api, 'get').mockImplementation(async (path) => {
    if (path === '/modules/module-1/slides.pptx') return { data: pptxBlob }
    if (path === '/modules/module-1/scorm.zip') {
      scormAttempts += 1
      if (scormAttempts === 1) throw refused
      return { data: zipBlob }
    }
    return { data: {} }
  })
  const createURL = vi.fn((b: Blob) => (b === pptxBlob ? 'blob:slides' : 'blob:scorm'))
  const revokeURL = vi.fn()
  vi.stubGlobal('URL', { createObjectURL: createURL, revokeObjectURL: revokeURL })
  const downloads: { href: string; download: string }[] = []
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ href: this.href, download: this.download })
  })
  try {
    renderPage()
    expect(await screen.findByText(/chưa liên kết với moodle/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^xuất bản lên moodle$/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/gemini-3.8-flash/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Tải slide PPTX' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
    expect(request).toHaveBeenCalledWith('/modules/module-1/slides.pptx', { responseType: 'blob' })
    expect(createURL).toHaveBeenCalledWith(pptxBlob)
    expect(revokeURL).toHaveBeenCalledWith('blob:slides')
    expect(downloads[0]).toEqual({ href: 'blob:slides', download: 'Test Module.pptx' })

    await userEvent.click(screen.getByRole('button', { name: 'Tải gói SCORM (.zip)' }))
    expect(await screen.findByText('Còn 1 câu hỏi cần xem lại (quiz 1). Lý do ghi dưới từng câu; hãy sửa trước khi xuất gói SCORM.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Tải gói SCORM (.zip)' }))
    await waitFor(() => expect(click).toHaveBeenCalledTimes(2))
    expect(request).toHaveBeenCalledWith('/modules/module-1/scorm.zip', { responseType: 'blob' })
    expect(downloads[1]).toEqual({ href: 'blob:scorm', download: 'Test Module - SCORM.zip' })
  } finally {
    request.mockRestore()
    click.mockRestore()
    vi.unstubAllGlobals()
  }
})

it('labels a control module and hides SCORM, Moodle publishing, and chat edit even when linked', async () => {
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, is_control: true, moodle_course_id: 6 })
  vi.mocked(api.getMoodleSections).mockResolvedValue([])
  renderPage()
  expect(await screen.findByText('Bản đối chứng cho nghiên cứu, không dùng tài liệu')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Tải gói SCORM (.zip)' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^xuất bản lên moodle$/i })).not.toBeInTheDocument()
  expect(api.publishToMoodle).not.toHaveBeenCalled()
  expect(screen.queryByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })).not.toBeInTheDocument()
})

describe('ModulePreviewPage - publish to Moodle', () => {
  it('says when a module already went to Moodle and publishes again only after the lecturer confirms', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6, moodle_published_at: '2026-11-18T03:00:00Z' })
    vi.mocked(api.getMoodleSections).mockResolvedValue([{ sectionnum: 0, name: 'General' }])
    vi.mocked(api.publishToMoodle).mockResolvedValue({ moodle_course_id: 6, book_cmid: 42 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()

    const button = await screen.findByRole('button', { name: /^xuất bản lại$/i })
    expect(screen.getByText(/đã xuất bản lên moodle lúc/i)).toBeInTheDocument()

    await userEvent.click(button)
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(api.publishToMoodle).not.toHaveBeenCalled()

    await userEvent.click(button)
    await waitFor(() => expect(api.publishToMoodle).toHaveBeenCalledWith('module-1', 0, true))
  })

  it('shows an error and hides publishing when access cannot be confirmed again after failure', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6 })
    vi.mocked(api.getMoodleSections).mockResolvedValueOnce([]).mockRejectedValue(new Error('no access'))
    vi.mocked(api.publishToMoodle).mockRejectedValue(new Error('boom'))

    renderPage()

    const button = await screen.findByRole('button', { name: /^xuất bản lên moodle$/i })
    await userEvent.click(button)

    expect(await screen.findByText(/xuất bản lên moodle thất bại/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^xuất bản lên moodle$/i })).not.toBeInTheDocument()
  })
})

describe('ModulePreviewPage - publish refused by the module', () => {
  it('brings the form back once access is confirmed again, after a refusal for questions to review', async () => {
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 6 })
    vi.mocked(api.getMoodleSections).mockResolvedValue([])
    const refusal = 'Còn 1 câu hỏi cần xem lại (quiz 1). Lý do ghi dưới từng câu; hãy sửa trước khi đẩy lên Moodle.'
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = { status: 422, statusText: '', data: { detail: refusal }, headers: {}, config }
    vi.mocked(api.publishToMoodle).mockRejectedValue(new AxiosError('422', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^xuất bản lên moodle$/i }))

    expect(await screen.findByText(refusal)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^xuất bản lên moodle$/i })).toBeEnabled()
    expect(api.getMoodleSections).toHaveBeenCalledTimes(2)
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

  it('shows examples, ordered quiz questions, hides AI reasoning, and saves edited explanation 2', async () => {
    vi.mocked(api.getModule).mockResolvedValue(structured)
    vi.mocked(api.updateComponentContent).mockResolvedValue({ id: 'explanation-1', content: {} })
    const user = userEvent.setup()

    renderPage()

    for (const text of ['BỐI-CẢNH-MỘT', 'PHÂN-TÍCH-MỘT', 'BỐI-CẢNH-HAI', 'PHÂN-TÍCH-HAI']) {
      expect(await screen.findByText(text)).toBeInTheDocument()
    }

    const first = screen.getByText('CÂU-HỎI-MỘT')
    const second = screen.getByText('CÂU-HỎI-HAI')
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const quizCard = first.closest('section') as HTMLElement
    const firstExplanation = within(quizCard).getByText('LỜI-GIẢI-MỘT')
    expect(firstExplanation).toBeVisible()
    expect(screen.queryByText('LẬP-LUẬN-MỘT')).not.toBeInTheDocument()
    expect(first.compareDocumentPosition(firstExplanation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(firstExplanation.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const explanationCard = screen.getByRole('heading', { name: 'Giải thích đáp án' }).closest('section') as HTMLElement
    await user.click(within(explanationCard).getByRole('button', { name: 'Sửa' }))
    const field = within(explanationCard).getByLabelText('Giải thích Câu kiểm tra 2')
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
  it('renders sub-objectives, Markdown headings/lists without images, overridden explanations, and CRAG flags', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      sub_objectives: ['MỤC-TIÊU-CON-A', 'MỤC-TIÊU-CON-B'],
      components: [
        {
          id: 'content-1',
          type: 'content',
          content: {
            markdown: '## TIÊU-ĐỀ-PHẦN\n- ý đầu tiên\n\n## Khái niệm\n\nA.\n\n### Chi tiết\n\nB.\n\n#### Ghi chú\n\nC.\n\nBài học. ![](https://evil.example/p)',
          },
          questions: [],
        },
        {
          id: 'quiz-1',
          type: 'quiz',
          content: {
            questions: [
              { passed: true },
              { passed: false, fail_reason: "Bloom's level fail: chỉ là nhận diện; Wording fail: Phương án 0 dài hơn; dễ đoán" },
            ],
          },
          questions: [
            { id: 'q1', position: 0, stem: 'CÂU-MỘT', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'BẢN-LÚC-SINH' },
            { id: 'q2', position: 1, stem: 'CÂU-HAI', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'e' },
          ],
        },
        {
          id: 'explanation-1',
          type: 'explanation',
          content: { items: [{ component: 'quiz', position: 0, reasoning: 'r', explanation_text: 'BẢN-ĐÃ-SỬA' }] },
          questions: [],
        },
        {
          id: 'practice-1',
          type: 'practice',
          content: { self_checked: false, questions: [{ passed: true }] },
          questions: [
            { id: 'p1', position: 0, stem: 'CÂU-LUYỆN-TẬP', choices: { options: ['A', 'B', 'C', 'D'] }, answer_key: '0', explanation: 'e' },
          ],
        },
      ],
    })

    const { container } = renderPage()

    expect(await screen.findByText('MỤC-TIÊU-CON-A')).toBeInTheDocument()
    expect(screen.getByText('MỤC-TIÊU-CON-B')).toBeInTheDocument()
    expect(screen.getByText('Understand X')).toBeInTheDocument()
    expect(screen.queryByText(/^Bloom:/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^understand$/i)).not.toBeInTheDocument()

    expect(screen.getByRole('heading', { name: 'TIÊU-ĐỀ-PHẦN' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Nội dung', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Khái niệm', level: 3 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chi tiết', level: 4 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ghi chú', level: 5 })).toBeInTheDocument()
    const content = screen.getByRole('heading', { name: 'Nội dung', level: 2 }).closest('section') as HTMLElement
    expect(within(content).getByRole('listitem')).toHaveTextContent('ý đầu tiên')
    expect(screen.queryByText(/## TIÊU-ĐỀ-PHẦN/)).not.toBeInTheDocument()
    expect(screen.getByText(/Bài học\./)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()

    const quizCard = screen.getByText('CÂU-MỘT').closest('section') as HTMLElement
    expect(within(quizCard).getByText('BẢN-ĐÃ-SỬA')).toBeInTheDocument()
    expect(within(quizCard).queryByText('BẢN-LÚC-SINH')).not.toBeInTheDocument()
    expect(within(quizCard).queryByText('Các câu hỏi này chưa được kiểm với tài liệu nguồn.')).not.toBeInTheDocument()
    expect(within(quizCard).getAllByText('Cần xem lại')).toHaveLength(1)
    const reasons = within(quizCard).getByRole('list', { name: 'Lý do câu 2 cần xem lại' })
    expect(within(reasons).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Chưa đúng mức Bloom: chỉ là nhận diện',
      'Cách diễn đạt: Phương án 0 dài hơn; dễ đoán',
    ])

    const practiceCard = screen.getByText('CÂU-LUYỆN-TẬP').closest('section') as HTMLElement
    expect(within(practiceCard).getByText('Các câu hỏi này chưa được kiểm với tài liệu nguồn.')).toBeInTheDocument()
    expect(within(practiceCard).queryByText('Cần xem lại')).not.toBeInTheDocument()
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
  const button = await screen.findByRole('button', { name: /^xuất bản lên moodle$/i })
  vi.mocked(api.getModule).mockResolvedValue(baseModule)
  await userEvent.click(button)
  expect(await screen.findByText(message)).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: /liên kết ở trang môn học/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^xuất bản lên moodle$/i })).not.toBeInTheDocument()
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

describe('ModulePreviewPage - forms', () => {
  const withComponent = (component: ModuleDetail['components'][number]): ModuleDetail => ({ ...baseModule, components: [component] })

  it('validates chat input, keeps input on failure, carries session_id, clarifies without refresh, and refreshes title & editor draft on applied edit', async () => {
    const withContent = (title: string, markdown: string): ModuleDetail => ({
      ...baseModule,
      title,
      components: [{ id: 'content-1', type: 'content', content: { markdown }, questions: [] }],
    })
    vi.mocked(api.getModule)
      .mockResolvedValueOnce(withContent('Test Module', 'Bản gốc dài'))
      .mockResolvedValue(withContent('Updated module', 'Bản rút gọn'))
    vi.mocked(api.chatEditModule)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ session_id: 'session-1', applied: false, reply: 'Bạn muốn sửa phần nào?', edit_count: 0 })
      .mockResolvedValueOnce({ session_id: 'session-1', applied: false, reply: 'Rõ rồi.', edit_count: 0 })
      .mockResolvedValueOnce({ session_id: 'session-1', applied: true, reply: 'Đã sửa slide 1.', edit_count: 1 })

    renderPage()
    const field = await screen.findByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })

    await userEvent.type(field, '   ')
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu' })).toBeDisabled()
    fireEvent.submit(field.closest('form')!)
    expect(await screen.findByText('Vui lòng nhập yêu cầu chỉnh sửa')).toBeInTheDocument()
    expect(api.chatEditModule).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: 'a'.repeat(4001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Yêu cầu tối đa 4000 ký tự')).toBeInTheDocument()
    expect(api.chatEditModule).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: 'Sửa lại' } })
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Chỉnh sửa thất bại. Vui lòng thử lại.')).toBeInTheDocument()
    expect(field).toHaveValue('Sửa lại')

    await userEvent.clear(field)
    await userEvent.type(field, '  Sửa lại ')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Bạn muốn sửa phần nào?')).toBeInTheDocument()
    expect(api.chatEditModule).toHaveBeenLastCalledWith('module-1', 'Sửa lại', undefined)
    expect(api.getModule).toHaveBeenCalledTimes(1)
    expect(field).toHaveValue('')

    await userEvent.type(field, 'Slide 3')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Rõ rồi.')).toBeInTheDocument()
    expect(api.chatEditModule).toHaveBeenLastCalledWith('module-1', 'Slide 3', 'session-1')
    expect(api.getModule).toHaveBeenCalledTimes(1)

    await userEvent.type(field, 'Rút gọn nội dung')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))
    expect(await screen.findByText('Đã sửa slide 1.')).toBeInTheDocument()
    expect(await screen.findByText('Mô-đun: Updated module')).toBeInTheDocument()
    expect(await screen.findByText('Bản rút gọn')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    expect(screen.getByDisplayValue('Bản rút gọn')).toBeInTheDocument()
  })

  // From the keyboard, as in CoursesPage.test: in jsdom a Radix trigger can stop opening on a mouse press.
  async function chooseRename() {
    ;(await screen.findByRole('button', { name: /^Thao tác khác với/ })).focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Đổi tên' }))
  }

  it('validates title length, restores on cancel, keeps open on failure, and renames heading on save', async () => {
    vi.mocked(api.getModule).mockResolvedValue(baseModule)
    vi.mocked(api.updateModuleTitle)
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce({ id: 'module-1', title: 'Tên mới' })
    renderPage()

    await chooseRename()
    const field = screen.getByRole('textbox', { name: 'Tên mô-đun' })
    expect(field).toHaveValue('Test Module')
    fireEvent.change(field, { target: { value: 'a'.repeat(201) } })
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText('Tên mô-đun tối đa 200 ký tự')).toBeInTheDocument()
    expect(api.updateModuleTitle).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await chooseRename()
    const reopened = screen.getByRole('textbox', { name: 'Tên mô-đun' })
    expect(reopened).toHaveValue('Test Module')

    await userEvent.type(reopened, ' 2')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText(/không thể lưu/i)).toBeInTheDocument()
    expect(reopened).toHaveValue('Test Module 2')

    await userEvent.clear(reopened)
    await userEvent.type(reopened, 'Tên mới')
    vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, title: 'Tên mới' })
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateModuleTitle).toHaveBeenCalledWith('module-1', 'Tên mới'))
    expect(await screen.findByRole('heading', { name: 'Mô-đun: Tên mới' })).toBeInTheDocument()
  })

  it('discards Content edits on cancel, keeps editor open on [S99] failure, and preserves extra fields on save', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({
      id: 'content-1', type: 'content', content: { markdown: 'Bản gốc', extra: 1, source_map: {} }, questions: [],
    }))
    vi.mocked(api.updateComponentContent)
      .mockRejectedValueOnce(new Error('Content cites unknown source IDs: S99'))
      .mockResolvedValueOnce({ id: 'content-1', content: {} })
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Nội dung' }), ' thêm')
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    const field = screen.getByRole('textbox', { name: 'Nội dung' })
    expect(field).toHaveValue('Bản gốc')

    fireEvent.change(field, { target: { value: 'Revised. [S99]' } })
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText(/không thể lưu/i)).toBeInTheDocument()
    expect(field).toHaveValue('Revised. [S99]')

    await userEvent.clear(field)
    await userEvent.type(field, 'Bản sửa')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalledWith('module-1', 'content-1', {
      markdown: 'Bản sửa', extra: 1, source_map: {},
    }))
  })

  it('keeps slides editor open on failure, clears alert on cancel, and saves edited bullets preserving notes', async () => {
    vi.mocked(api.getModule).mockResolvedValue(withComponent({
      id: 'slides-1',
      type: 'slides',
      content: { slides: [{ title: 'Một', bullets: ['a', 'b'], notes: 'n' }, { title: 'Hai', bullets: ['c'] }] },
      questions: [],
    }))
    vi.mocked(api.updateComponentContent)
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValueOnce({ id: 'slides-1', content: {} })
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
    const title = screen.getByRole('textbox', { name: 'Tiêu đề slide 1' })
    await userEvent.type(title, ' mới')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText(/không thể lưu/i)).toBeInTheDocument()
    expect(title).toHaveValue('Một mới')
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Sửa' }))
    const reopenedTitle = screen.getByRole('textbox', { name: 'Tiêu đề slide 1' })
    await userEvent.clear(reopenedTitle)
    await userEvent.type(reopenedTitle, 'Một mới')
    const bullets = screen.getByRole('textbox', { name: 'Nội dung slide 1' })
    expect(bullets).toHaveValue('a\nb')
    await userEvent.type(bullets, '{Enter}c')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenLastCalledWith('module-1', 'slides-1', {
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
    const question = { id: 'q1', position: 0, stem: 'Original stem', choices: { options: ['A', 'B'] }, answer_key: '1', explanation: 'Original explanation' }
    const quiz = withComponent({ id: 'quiz-1', type: 'quiz', content: {}, questions: [question] })

    it('validates stem and choices, checks the saved answer radio, and reports save failure', async () => {
      vi.mocked(api.getModule).mockResolvedValue(quiz)
      vi.mocked(api.updateQuestion).mockRejectedValue(new Error('boom'))
      renderPage()

      await userEvent.click(await screen.findByRole('button', { name: 'Sửa' }))
      expect(screen.getByRole('radio', { name: 'Đáp án B' })).toBeChecked()
      expect(screen.getByRole('radio', { name: 'Đáp án A' })).not.toBeChecked()

      const stem = screen.getByRole('textbox', { name: 'Câu hỏi' })
      await userEvent.clear(stem)
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Vui lòng nhập câu hỏi')).toBeInTheDocument()
      expect(api.updateQuestion).not.toHaveBeenCalled()

      await userEvent.type(stem, 'Original stem')
      const choiceB = screen.getByRole('textbox', { name: 'Phương án B' })
      await userEvent.clear(choiceB)
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Vui lòng nhập phương án')).toBeInTheDocument()
      expect(api.updateQuestion).not.toHaveBeenCalled()

      await userEvent.type(choiceB, 'B')
      await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
      expect(await screen.findByText('Lưu câu hỏi thất bại')).toBeInTheDocument()
      expect(api.updateQuestion).toHaveBeenCalledWith('module-1', 'q1', expect.objectContaining({ answer_key: 1 }))
    })
  })

  describe('publishing to Moodle', () => {
    const linked = { ...baseModule, moodle_course_id: 6 }
    const sections = [{ sectionnum: 0, name: 'General' }, { sectionnum: 1, name: 'Tuần 1' }, { sectionnum: 2, name: 'Tuần 2' }]
    const result = { moodle_course_id: 6, book_cmid: 1 }

    it('fetches sections once, pushes to section 2, disables against double-click, and lists created activities', async () => {
      vi.mocked(api.getModule).mockResolvedValue(linked)
      vi.mocked(api.getMoodleSections).mockResolvedValue(sections)
      vi.mocked(api.publishToMoodle).mockResolvedValue({
        moodle_course_id: 6,
        book_cmid: 42,
        practice_cmid: 43,
        quiz_cmid: 44,
      })
      renderPage()

      const button = await screen.findByRole('button', { name: /^xuất bản lên moodle$/i })
      expect(button).toBeEnabled()
      expect(api.getMoodleSections).toHaveBeenCalledOnce()

      await userEvent.selectOptions(screen.getByRole('combobox'), '2')
      await userEvent.click(button)

      await waitFor(() => expect(api.publishToMoodle).toHaveBeenCalledWith('module-1', 2, false))
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /đã xuất bản lên moodle/i })).toBeDisabled()
      })
      expect(api.publishToMoodle).toHaveBeenCalledTimes(1)
      // Says what was created, in the order it was created.
      expect(screen.getByText(/sách nội dung, bài luyện tập, quiz\./i)).toBeInTheDocument()

      // Clicking again (e.g. a stray extra click before React re-renders)
      // must not fire a second request -- moodle_client.create_book/create_quiz
      // are not idempotent, a second call would create duplicate content.
      await userEvent.click(screen.getByRole('button', { name: /đã xuất bản lên moodle/i }))
      expect(api.publishToMoodle).toHaveBeenCalledTimes(1)
    })

    it('pushes to a new section, numbered after the existing ones, and hides the drop-down', async () => {
      vi.mocked(api.getModule).mockResolvedValue(linked)
      vi.mocked(api.getMoodleSections).mockResolvedValue(sections)
      vi.mocked(api.publishToMoodle).mockResolvedValue(result)
      renderPage()
      await screen.findByRole('combobox')
      await userEvent.click(screen.getByRole('checkbox', { name: /tạo chủ đề mới/i }))
      expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: /^xuất bản lên moodle$/i }))
      await waitFor(() => expect(api.publishToMoodle).toHaveBeenCalledWith('module-1', 3, false))
    })
  })
})

it('hides Moodle publishing when live permission lookup fails', async () => {
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 72 })
  vi.mocked(api.getMoodleSections).mockRejectedValue(new Error('Denied'))
  renderPage()
  expect(await screen.findByText('Không tải được danh sách phần học Moodle')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Xuất bản lên Moodle' })).not.toBeInTheDocument()
})

it('says once that Moodle cannot be reached, not again under the publish form', async () => {
  const unreachable = { isAxiosError: true, response: { data: { detail: 'Không kết nối được tới Moodle, vui lòng thử lại sau.' } } }
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 72 })
  vi.mocked(api.getMoodleAccount).mockRejectedValue(unreachable)
  vi.mocked(api.getMoodleSections).mockRejectedValue(unreachable)
  renderPage()
  expect(await screen.findByText('Không kết nối được tới Moodle, vui lòng thử lại sau.')).toBeInTheDocument()
  expect(screen.getAllByText('Không kết nối được tới Moodle, vui lòng thử lại sau.')).toHaveLength(1)
  expect(api.getMoodleSections).not.toHaveBeenCalled()
})

it('does not ask Moodle for sections where this environment has no Moodle', async () => {
  vi.mocked(api.getModule).mockResolvedValue({ ...baseModule, moodle_course_id: 72 })
  vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'teacher@example.com', status: 'unavailable' })
  renderPage()
  expect(await screen.findByText('Chưa kết nối Moodle')).toBeInTheDocument()
  expect(api.getMoodleSections).not.toHaveBeenCalled()
})

it('renders numbered inline citations in reading order and opens the exact chunk', async () => {
  vi.mocked(api.getModule).mockResolvedValue({
    ...baseModule,
    claim_citations: [
      { claim: 'Fact.', source_id: 'S2', locator: 'page 2', chunk_content: 'Second physical chunk', resolution: 'resolved' },
      { claim: 'Fact.', source_id: 'S1', locator: 'page 1', chunk_content: 'First physical chunk', resolution: 'resolved' },
    ],
    components: [{ id: 'content-1', type: 'content', questions: [], content: {
      markdown: '- Fact. [S2][S1]\n- Repeated source. [S2]', source_map: {},
    } }],
  } as ModuleDetail)
  renderPage()
  const buttons = await screen.findAllByRole('button', { name: 'Nguồn 1' })
  expect(buttons).toHaveLength(2)
  expect(screen.getByRole('button', { name: 'Nguồn 2' })).toHaveTextContent('[2]')
  await userEvent.click(buttons[0])
  expect(screen.getByRole('dialog')).toHaveTextContent('Second physical chunk')
  expect(screen.getByRole('dialog')).not.toHaveTextContent('First physical chunk')
  await userEvent.click(screen.getByRole('button', { name: 'Đóng nguồn' }))
  await userEvent.click(screen.getByRole('button', { name: 'Nguồn 2' }))
  expect(screen.getByRole('dialog')).toHaveTextContent('First physical chunk')
})

describe('ModulePreviewPage - load failure', () => {
  const failure = (status: number) => {
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = { status, statusText: '', data: { detail: 'x' }, headers: {}, config }
    return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse)
  }

  it('says a module that does not exist was not found', async () => {
    vi.mocked(api.getModule).mockRejectedValue(failure(404))
    renderPage()
    expect(await screen.findByText('Không tìm thấy mô-đun.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Thử lại' })).not.toBeInTheDocument()
  })

  it('says the module could not be loaded when the server fails, and retries', async () => {
    vi.mocked(api.getModule).mockRejectedValueOnce(failure(500)).mockResolvedValue(baseModule)
    renderPage()
    expect(await screen.findByText('Không tải được mô-đun.')).toBeInTheDocument()
    expect(screen.queryByText('Không tìm thấy mô-đun.')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }))

    expect(await screen.findByText('Mô-đun: Test Module')).toBeInTheDocument()
  })
})

describe('ModulePreviewPage - keyboard focus', () => {
  it('keeps the focus in the editor when its buttons are swapped for fields and back', async () => {
    vi.mocked(api.getModule).mockResolvedValue({
      ...baseModule,
      components: [{ id: 'content-1', type: 'content', content: { markdown: 'Lesson' }, questions: [] }],
    })
    vi.mocked(api.updateComponentContent).mockResolvedValue(undefined as never)
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Lesson')
    const part = document.getElementById('part-content')!
    within(part).getByRole('button', { name: 'Sửa' }).focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(within(part).getByRole('textbox')).toHaveFocus())
    await user.tab()
    await user.tab()
    expect(within(part).getByRole('button', { name: 'Hủy' })).toHaveFocus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(within(part).getByRole('button', { name: 'Sửa' })).toHaveFocus())
    within(part).getByRole('button', { name: 'Sửa' }).focus()
    await user.keyboard('{Enter}')
    await user.tab()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.updateComponentContent).toHaveBeenCalled())
    await waitFor(() => expect(within(part).getByRole('button', { name: 'Sửa' })).toHaveFocus())
  })
})
