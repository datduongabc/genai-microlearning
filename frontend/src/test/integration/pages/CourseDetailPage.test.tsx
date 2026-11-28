import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CourseDetailPage } from '../../../pages/CourseDetailPage'
import { AuthProvider } from '../../../auth/AuthContext'
import * as api from '../../../api'
import type { Course, DocumentInfo, ModuleSummary } from '../../../api'

// Every function of the module is replaced except the one that words an error: that is
// real logic the pages rely on, not a call to the server.
// Creating content goes through the document's "Tạo nội dung" menu.
// Opened from the keyboard: in jsdom a Radix trigger stopped opening on a mouse press after the
// first test of a group, while Enter works every time and is a path a lecturer uses too.
async function openMenu(trigger: RegExp) {
  ;(await screen.findByRole('button', { name: trigger })).focus()
  await userEvent.keyboard('{Enter}')
}
async function openCreate() {
  await openMenu(/tạo nội dung từ/i)
}
async function chooseCreate(item: RegExp) {
  await openCreate()
  await userEvent.click(screen.getByRole('menuitem', { name: item }))
}
// The item as the menu showed it; the menu is closed again so the rest of the test can click the page.
async function createItem(item: RegExp) {
  await openCreate()
  const found = await screen.findByRole('menuitem', { name: item })
  await userEvent.keyboard('{Escape}')
  return found
}

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && name !== 'apiErrorMessage' ? vi.fn() : value,
    ]),
  )
})

const unlinkedCourse: Course = {
  id: 'course-1',
  name: 'AI 101',
  created_at: '2026-01-01T00:00:00Z',
  moodle_course_id: null,
}

const sampleDocument: DocumentInfo = {
  id: 'doc-1',
  filename: 'syllabus.pdf',
  status: 'embedded',
  created_at: '2026-01-01T00:00:00Z',
  module_count: 0,
  generating: false,
  failures: [],
  extraction_warnings: [],
  learner_access: false,
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/courses/course-1']}>
        <Routes>
          <Route path="/courses/:courseId" element={<CourseDetailPage />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listDocuments).mockResolvedValue([])
  vi.mocked(api.listFlashcardSets).mockResolvedValue([])
  vi.mocked(api.listVideos).mockResolvedValue([])
  vi.mocked(api.getMediaModels).mockResolvedValue({
    voices: [{ alias: 'voice-heygen', label: 'HeyGen Voice 1 Turbo', price: 'khoảng $0,03 một video 8 phút' }],
    images: [
      { alias: 'image-nano-banana', label: 'Gemini Nano Banana 2.1', price: 'khoảng $0,037 một ảnh' },
      { alias: 'image-qwen', label: 'Qwen Image 2.1 Turbo', price: 'khoảng $0,018 một ảnh' },
    ],
    default_voice: 'voice-heygen', default_image: 'image-nano-banana',
  })
  // Ô chọn model gọi API này ngay khi trang mở, như listDocuments ở trên.
  vi.mocked(api.listModels).mockResolvedValue({ models: [], default: null })
  vi.mocked(api.listModuleConcepts).mockResolvedValue([{ name: 'Tìm kiếm', summary: 'Tìm trong không gian trạng thái.' }])
  // The course chat loads its last conversation on open.
  vi.mocked(api.getLatestCourseChat).mockResolvedValue({ session_id: null, messages: [] })
  vi.mocked(api.listChatFeedback).mockResolvedValue([])
  // The Moodle account line asks too; without an answer it threw after the test ended.
  vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: '', status: 'not_linked' })
})

const axiosFailure = (status: number, detail: string) => {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data: { detail }, headers: {}, config }
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse)
}

const moodleCourses = [
  { id: 6, fullname: 'Introduction to Artificial Intelligence', shortname: 'Intro2AI' },
  { id: 7, fullname: 'Databases', shortname: 'DB' },
]

describe('CourseDetailPage - Moodle linking', () => {
  beforeEach(() => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'linked' })
    vi.mocked(api.listMoodleCourses).mockResolvedValue(moodleCourses)
  })

  it('offers authorized Moodle courses, validates empty submit, reports link error, and links the chosen course', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([unlinkedCourse])
      .mockResolvedValueOnce([{ ...unlinkedCourse, moodle_course_id: 6 }])
    vi.mocked(api.linkMoodleCourse)
      .mockRejectedValueOnce(axiosFailure(404, 'Không tìm thấy khóa học Moodle này'))
      .mockResolvedValueOnce({ moodle_course_id: 6 })

    renderPage()

    const select = await screen.findByRole('combobox', { name: /khóa học moodle/i })
    expect(screen.getByRole('button', { name: 'Liên kết' })).toBeDisabled()
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    expect(within(select).getByRole('option', { name: 'Databases (DB)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /tạo course moodle/i })).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/tìm course moodle/i)).not.toBeInTheDocument()

    fireEvent.submit(select.closest('form')!)
    expect(await screen.findByText('Vui lòng chọn một khóa học Moodle')).toBeInTheDocument()
    expect(api.linkMoodleCourse).not.toHaveBeenCalled()

    await userEvent.selectOptions(select, '6')
    await userEvent.click(screen.getByRole('button', { name: 'Liên kết' }))
    expect(await screen.findByText('Không tìm thấy khóa học Moodle này')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Liên kết' }))
    await waitFor(() => {
      expect(api.linkMoodleCourse).toHaveBeenCalledWith('course-1', 6)
    })
    expect(await screen.findByText('Đã liên kết với khóa học Moodle #6.')).toBeInTheDocument()
    // Linked, the course has no choice left to make, so the line goes.
    await waitFor(() => expect(screen.queryByRole('combobox', { name: /khóa học moodle/i })).not.toBeInTheDocument())
  })

  it('says so when the list of Moodle courses cannot be loaded', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listMoodleCourses).mockRejectedValue(axiosFailure(502, 'Moodle không phản hồi'))

    renderPage()

    expect(await screen.findByText('Moodle không phản hồi')).toBeInTheDocument()
  })

  it.each([
    {
      caseName: 'the account is not linked',
      account: { email: 'lan@x.edu', status: 'not_linked' as const },
      course: unlinkedCourse,
      checkAccountMenu: true,
      checkUnlink: false,
    },
    {
      caseName: 'the course is already linked',
      account: { email: 'lan@x.edu', status: 'linked' as const },
      course: { ...unlinkedCourse, moodle_course_id: 6 },
      checkAccountMenu: false,
      checkUnlink: true,
    },
    {
      caseName: 'the environment has no Moodle',
      account: { email: 'lan@x.edu', status: 'unavailable' as const },
      course: unlinkedCourse,
      checkAccountMenu: false,
      checkUnlink: false,
    },
  ])('shows no Moodle course line when $caseName', async ({ account, course, checkAccountMenu, checkUnlink }) => {
    vi.mocked(api.listCourses).mockResolvedValue([course])
    vi.mocked(api.getMoodleAccount).mockResolvedValue(account)

    renderPage()

    await screen.findByRole('heading', { name: 'Tài liệu' })
    await waitFor(() => expect(api.getMoodleAccount).toHaveBeenCalled())
    expect(screen.queryByText('Khóa học Moodle:')).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /khóa học moodle/i })).not.toBeInTheDocument()
    if (checkAccountMenu) {
      // The account is linked from the account menu, not a second time on the page.
      expect(screen.queryByRole('region', { name: 'Tài khoản Moodle' })).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Email Moodle')).not.toBeInTheDocument()
      expect(api.listMoodleCourses).not.toHaveBeenCalled()
    }
    if (checkUnlink) {
      expect(screen.queryByRole('button', { name: /hủy liên kết/i })).not.toBeInTheDocument()
    }
  })

  it('stops offering courses when the server says the account link must be made again', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])

    renderPage()
    expect(await screen.findByRole('combobox', { name: /khóa học moodle/i })).toBeInTheDocument()

    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    act(() => {
      window.dispatchEvent(new Event(api.MOODLE_ACCOUNT_CHANGED_EVENT))
    })

    await waitFor(() => expect(screen.queryByRole('combobox', { name: /khóa học moodle/i })).not.toBeInTheDocument())
  })
})

describe('CourseDetailPage - delete document', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('asks for confirmation, shows error when delete fails, and deletes and refreshes when confirmed', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockResolvedValueOnce([])
    vi.mocked(api.deleteDocument)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(undefined)
    vi.spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(true)

    renderPage()

    await openMenu(/thao tác khác với/i)
    await userEvent.click(screen.getByRole('menuitem', { name: /xóa tài liệu/i }))
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(api.deleteDocument).not.toHaveBeenCalled()
    expect(screen.getByText('syllabus.pdf')).toBeInTheDocument()

    await openMenu(/thao tác khác với/i)
    await userEvent.click(screen.getByRole('menuitem', { name: /xóa tài liệu/i }))
    expect(await screen.findByText(/xóa tài liệu thất bại/i)).toBeInTheDocument()
    expect(screen.getByText('syllabus.pdf')).toBeInTheDocument()

    await openMenu(/thao tác khác với/i)
    await userEvent.click(screen.getByRole('menuitem', { name: /xóa tài liệu/i }))
    await waitFor(() => {
      expect(api.deleteDocument).toHaveBeenCalledWith('doc-1')
    })
    await waitFor(() => {
      expect(screen.queryByText('syllabus.pdf')).not.toBeInTheDocument()
    })
  })

  it('says once, without pages, how much was redacted and that an older file has the same name', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, extraction_warnings: [
      { code: 'ocr_failed', page: 3 }, { code: 'redacted', page: 0, count: 2 }, { code: 'same_name', page: 0 },
    ] }])
    renderPage()

    expect(await screen.findByText(/đã che 2 thông tin nhạy cảm/i)).toHaveClass('doc-note')
    expect(screen.getByText(/đã có tài liệu cùng tên/i)).toHaveClass('doc-warning')
    expect(screen.getByText(/không nhận dạng được chữ ở trang 3\./i)).toBeInTheDocument()
  })

  it('shows learners\' chat feedback to the lecturer, problems first and without names', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    const item = { answered_at: '2026-11-28T01:00:00Z', feedback_at: '2026-11-28T01:05:00Z', note: null,
      citations: [{ chunk_id: 'c1', source_ref: '{"page": 4}' }], flagged_citations: [] as number[] }
    vi.mocked(api.listChatFeedback).mockResolvedValue([
      { ...item, message_id: 'm1', question: 'Heap là gì?', answer: 'Một cây.', feedback: 'helpful' },
      { ...item, message_id: 'm2', question: 'Stack là gì?', answer: 'Một hàng đợi.', feedback: 'wrong_citation',
        flagged_citations: [1], note: 'Nguồn nói về heap' },
    ])
    renderPage()

    expect(await screen.findByText(/phản hồi của sinh viên về chat: 1 chưa đúng, 1 hữu ích/i)).toBeInTheDocument()
    const questions = screen.getAllByText(/là gì\?$/).map((el) => el.textContent)
    expect(questions).toEqual(['Stack là gì?', 'Heap là gì?'])
    expect(screen.getByText(/trích dẫn bị báo sai: \[1\] trang 4/i)).toBeInTheDocument()
    expect(screen.getByText('Ghi chú: Nguồn nói về heap')).toBeInTheDocument()
  })

  it('opens a document to learners from its row, and puts the box back when the server refuses', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.setLearnerAccess).mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('boom'))
    renderPage()

    const box = await screen.findByRole('checkbox', { name: /sinh viên được hỏi chat về tài liệu này/i })
    expect(box).not.toBeChecked()
    await userEvent.click(box)
    expect(api.setLearnerAccess).toHaveBeenCalledWith('doc-1', true)
    await waitFor(() => expect(box).toBeChecked())

    await userEvent.click(box)
    expect(api.setLearnerAccess).toHaveBeenLastCalledWith('doc-1', false)
    expect(await screen.findByText(/không đổi được quyền hỏi/i)).toBeInTheDocument()
    await waitFor(() => expect(box).toBeChecked())
  })

  it('says the document is deleted, and only the reload failed, when the list cannot be reloaded afterwards', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockRejectedValueOnce(new Error('network down'))
    vi.mocked(api.deleteDocument).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await openMenu(/thao tác khác với/i)
    await userEvent.click(screen.getByRole('menuitem', { name: /xóa tài liệu/i }))

    expect(await screen.findByText(/đã xóa tài liệu "syllabus.pdf"/i)).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.getByText(/tài liệu đã được xóa/i)).toBeInTheDocument()
    expect(screen.queryByText(/xóa tài liệu thất bại/i)).not.toBeInTheDocument()
  })
})

describe('CourseDetailPage - upload availability', () => {
  const processingDocument: DocumentInfo = { ...sampleDocument, id: 'doc-2', filename: 'big.pdf', status: 'processing' }

  function uploadInput() {
    return document.querySelector('input[type="file"]') as HTMLInputElement
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('disables while loading, enables for ready/failed, validates size, shows 409 and upload progress, and stays disabled as uploaded', async () => {
    let resolveDocuments!: (docs: DocumentInfo[]) => void
    let resolveUpload!: (res: { id: string; status: string; job_id: string }) => void
    vi.mocked(api.listDocuments)
      .mockReturnValueOnce(new Promise<DocumentInfo[]>((resolve) => {
        resolveDocuments = resolve
      }))
      .mockResolvedValue([{ ...processingDocument, status: 'uploaded' }])
    vi.mocked(api.uploadDocument)
      .mockRejectedValueOnce(
        Object.assign(new Error('Request failed with status code 409'), {
          isAxiosError: true,
          response: { status: 409, data: { detail: 'Đang có tài liệu khác được xử lý' } },
        }),
      )
      .mockImplementationOnce(async (_course, _file, onProgress) => {
        onProgress?.(0.42)
        return new Promise((resolve) => {
          resolveUpload = resolve
        })
      })

    renderPage()

    expect(uploadInput()).toBeDisabled()
    await act(async () => resolveDocuments([sampleDocument, { ...sampleDocument, id: 'doc-3', status: 'failed' }]))
    expect(await screen.findByText('Tải tài liệu lên')).toBeInTheDocument()
    await waitFor(() => expect(uploadInput()).toBeEnabled())

    const oversized = new File(['x'], 'lecture.pdf', { type: 'application/pdf' })
    Object.defineProperty(oversized, 'size', { value: 25 * 1024 * 1024 + 1 })
    await userEvent.upload(uploadInput(), oversized)
    expect(await screen.findByText('Tài liệu tối đa 25 MB')).toBeInTheDocument()
    expect(api.uploadDocument).not.toHaveBeenCalled()
    expect(uploadInput()).toBeEnabled()

    await userEvent.upload(uploadInput(), new File(['x'], 'big.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText('Đang có tài liệu khác được xử lý')).toBeInTheDocument()

    const lectureFile = new File(['x'], 'lecture.mp4', { type: 'video/mp4' })
    await userEvent.upload(uploadInput(), lectureFile)
    expect(await screen.findByText('Đang tải lên 42%')).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()
    expect(api.uploadDocument).toHaveBeenLastCalledWith('course-1', lectureFile, expect.any(Function))

    await act(async () => resolveUpload({ id: 'doc-2', status: 'uploaded', job_id: 'job-1' }))
    expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()
  })

  it('stays disabled for uploaded and processing documents and becomes available once polling sees embedded', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([{ ...processingDocument, status: 'uploaded' }])
      .mockResolvedValueOnce([processingDocument])
      .mockResolvedValue([{ ...processingDocument, status: 'embedded' }])

    renderPage()
    expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })
    expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })

    await waitFor(() => expect(uploadInput()).toBeEnabled())
  })
})

describe('CourseDetailPage - module generation availability', () => {
  const generatingDocument: DocumentInfo = { ...sampleDocument, generating: true }
  const newModule: ModuleSummary = {
    id: 'module-1',
    title: 'Intro to search',
    learning_objective: null,
    bloom_level: null,
    is_control: false, component_count: 6,
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('offers creation, returns focus on Escape, cancels cleanly, shows 422 model error, and starts busy generation', async () => {
    vi.mocked(api.listDocuments).mockResolvedValueOnce([sampleDocument]).mockResolvedValue([generatingDocument])
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = {
      status: 422,
      data: { detail: "Model 'openai-fast' chưa dùng được: chưa có API key hợp lệ. Hãy chọn model khác." },
    }
    vi.mocked(api.generateModule)
      .mockRejectedValueOnce(new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse))
      .mockResolvedValueOnce({ job_id: 'job-1', status: 'queued' })

    renderPage()

    expect(await createItem(/^mô-đun$/i)).not.toHaveAttribute('aria-disabled')

    await chooseCreate(/^video$/i)
    expect(await screen.findByRole('dialog', { name: 'Tạo video' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.getByRole('button', { name: /tạo nội dung từ/i })).toHaveFocus())

    await chooseCreate(/^mô-đun$/i)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.generateModule).not.toHaveBeenCalled()

    await chooseCreate(/^mô-đun$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))
    expect(await screen.findByText(/chưa có API key hợp lệ/)).toBeInTheDocument()

    await chooseCreate(/^mô-đun$/i)
    await screen.findByRole('option', { name: 'Tìm kiếm (quan trọng nhất)' })
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    expect(api.generateModule).toHaveBeenLastCalledWith('doc-1', '', 'vi', '', { name: 'Tìm kiếm', summary: 'Tìm trong không gian trạng thái.' })
    expect(await createItem(/^mô-đun$/i)).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('Mô-đun mới')).toBeInTheDocument()
  })

  it('shows the button busy with a pending row when generating and reveals the new module once polling finishes', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([generatingDocument])
      .mockResolvedValue([{ ...sampleDocument, module_count: 1 }])
    vi.mocked(api.listModules).mockResolvedValueOnce([]).mockResolvedValue([newModule])

    renderPage()
    expect(await createItem(/^mô-đun$/i)).toHaveAttribute('aria-disabled', 'true')
    // The module being made has its row, as a set or a video being made has.
    expect(screen.getByText('Mô-đun mới')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await createItem(/^mô-đun$/i)).not.toHaveAttribute('aria-disabled')
    expect(await screen.findByText(/Intro to search/)).toBeInTheDocument()
    expect(screen.queryByText('Mô-đun mới')).not.toBeInTheDocument()
  })

  it('generates with the teacher saved default model preselected', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.listModels).mockResolvedValue({
      models: [{ alias: 'deepseek-flash', label: 'DeepSeek V4.1 Flash', provider: 'deepseek', note: null }],
      default: 'deepseek-flash',
    })
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })

    renderPage()
    // The model is chosen in the dialog, not on the page.
    await screen.findByText('syllabus.pdf')
    expect(screen.queryByRole('combobox', { name: /^mô hình$/i })).not.toBeInTheDocument()
    await chooseCreate(/^mô-đun$/i)
    expect(api.generateModule).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await waitFor(() =>
      expect((screen.getByRole('combobox', { name: /^mô hình$/i }) as HTMLSelectElement).value).toBe('deepseek-flash'),
    )
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /^ngôn ngữ đầu ra$/i }), 'en')
    await screen.findByRole('option', { name: 'Tìm kiếm (quan trọng nhất)' })
    await userEvent.type(screen.getByLabelText(/Yêu cầu thêm cho mô-đun/), 'Ví dụ: thêm ví dụ có lời giải từng bước')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    // The concept is the planner's first for the document, which the dialog picks until the lecturer picks another.
    expect(api.listModuleConcepts).toHaveBeenCalledWith('doc-1', 'en')
    expect(api.generateModule).toHaveBeenCalledWith('doc-1', 'deepseek-flash', 'en', 'Ví dụ: thêm ví dụ có lời giải từng bước',
      { name: 'Tìm kiếm', summary: 'Tìm trong không gian trạng thái.' })
  })
})

const flashcardSet: api.FlashcardSetSummary = {
  id: 'set-1', course_id: 'course-1', document_id: 'doc-1', focus: 'Mảng', output_language: 'vi', model_alias: 'gemini-flash',
  status: 'draft', card_count: 8, published_at: null, created_at: '2026-01-01T00:00:00Z', draft_version: 1,
}

describe('CourseDetailPage - flashcard sets', () => {
  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.listModules).mockResolvedValue([])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('lists flashcard sets under their document with their status', async () => {
    vi.mocked(api.listFlashcardSets).mockResolvedValue([
      flashcardSet,
      { ...flashcardSet, id: 'set-2', focus: null, status: 'generating', card_count: 0 },
      { ...flashcardSet, id: 'set-3', focus: 'Cây', status: 'failed', card_count: 0 },
      { ...flashcardSet, id: 'set-4', focus: 'Đồ thị', published_at: '2026-01-02T00:00:00Z' },
      { ...flashcardSet, id: 'set-5', document_id: 'doc-other', focus: 'Của tài liệu khác' },
    ])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument, { ...sampleDocument, id: 'doc-other', filename: 'other.pdf' }])

    renderPage()

    const list = await screen.findByRole('list', { name: 'Nội dung tạo từ syllabus.pdf' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(4)
    expect(within(items[0]).getByRole('link', { name: /Mảng/ })).toHaveAttribute('href', '/flashcard-sets/set-1')
    // A ready set is its kind and its name, as a module is; only a job running or failed says more.
    expect(items[0]).toHaveTextContent(/^FlashcardMảngXóa$/)
    expect(items[1]).toHaveTextContent('Toàn bộ tài liệu')
    expect(within(items[1]).getByText('Đang tạo…')).toHaveAttribute('data-status', 'processing')
    expect(within(items[2]).getByText('Tạo thất bại')).toHaveAttribute('data-status', 'failed')
    expect(items[3]).toHaveTextContent(/^FlashcardĐồ thịXóa$/)
    const other = screen.getByRole('list', { name: 'Nội dung tạo từ other.pdf' })
    expect(within(other).getAllByRole('listitem')).toHaveLength(1)
  })

  it('handles 409 conflict, shows a pending set while in-flight, and generates a set with focus', async () => {
    let answer!: (value: { id: string; job_id: string }) => void
    vi.mocked(api.generateFlashcards)
      .mockRejectedValueOnce(axiosFailure(409, 'Bộ thẻ này đang được sinh, vui lòng chờ.'))
      .mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      .mockResolvedValueOnce({ id: 'set-2', job_id: 'job-2' })
    vi.mocked(api.listFlashcardSets)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ ...flashcardSet, status: 'generating', card_count: 0 }])

    renderPage()

    await chooseCreate(/^flashcard$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))
    expect(await screen.findByText('Bộ thẻ này đang được sinh, vui lòng chờ.')).toBeInTheDocument()

    await chooseCreate(/^flashcard$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))
    const list = await screen.findByRole('list', { name: 'Nội dung tạo từ syllabus.pdf' })
    const pending = within(list).getByRole('listitem')
    expect(pending).toHaveTextContent('Toàn bộ tài liệu')
    expect(within(pending).getByText('Đang gửi yêu cầu…')).toHaveAttribute('data-status', 'processing')
    expect(await createItem(/^flashcard$/i)).toHaveAttribute('aria-disabled', 'true')

    answer({ id: 'set-1', job_id: 'job-1' })
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
    expect(screen.queryByText('Đang gửi yêu cầu…')).not.toBeInTheDocument()
    expect(await createItem(/^flashcard$/i)).not.toHaveAttribute('aria-disabled')

    await chooseCreate(/^flashcard$/i)
    // Each kind has its own dialog in the page, so the field is looked up in the open one.
    await userEvent.type(within(screen.getByRole('dialog', { name: 'Tạo flashcard' })).getByLabelText(/nên tập trung vào nội dung gì/), 'Mảng')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))

    expect(api.generateFlashcards).toHaveBeenLastCalledWith('doc-1', { focus: 'Mảng', outputLanguage: 'vi', modelAlias: '' })
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
  })

  it('says the module started, not that it failed, when only the list cannot be reloaded after it', async () => {
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    vi.mocked(api.listDocuments).mockResolvedValueOnce([sampleDocument]).mockRejectedValue(new Error('network down'))

    renderPage()
    await chooseCreate(/^mô-đun$/i)
    await screen.findByRole('option', { name: 'Tìm kiếm (quan trọng nhất)' })
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    expect(await screen.findByText('Đã bắt đầu tạo mô-đun.')).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.queryByText(/tạo mô-đun thất bại/i)).not.toBeInTheDocument()
  })

  it('says the set started, not that it failed, when only the list cannot be reloaded after it', async () => {
    vi.mocked(api.generateFlashcards).mockResolvedValue({ id: 'set-1', job_id: 'job-1' })
    vi.mocked(api.listFlashcardSets).mockResolvedValueOnce([]).mockRejectedValue(new Error('network down'))

    renderPage()
    await chooseCreate(/^flashcard$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))

    expect(await screen.findByText('Đã bắt đầu tạo flashcard.')).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.queryByText(/sinh flashcard thất bại/i)).not.toBeInTheDocument()
  })

  it('follows a generating set until the server reports it done', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listFlashcardSets)
      .mockResolvedValueOnce([{ ...flashcardSet, status: 'generating', card_count: 0 }])
      .mockResolvedValue([flashcardSet])

    renderPage()
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    await waitFor(() => expect(screen.queryByText('Đang tạo…')).not.toBeInTheDocument())
    expect(screen.getByRole('link', { name: 'Mảng' })).toBeInTheDocument()
  })

  it('shows why a generating set failed, with its retry, without a reload', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listFlashcardSets)
      .mockResolvedValueOnce([{ ...flashcardSet, status: 'generating', card_count: 0 }])
      .mockResolvedValue([{ ...flashcardSet, status: 'failed', card_count: 0 }])
    vi.mocked(api.listDocuments).mockResolvedValueOnce([sampleDocument]).mockResolvedValue([{
      ...sampleDocument,
      failures: [{ job_id: 'job-f', job_type: 'flashcard_generation', error: 'Không viết được thẻ nào dựa trên tài liệu.' }],
    }])

    renderPage()
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await screen.findByText('Không viết được thẻ nào dựa trên tài liệu.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Thử lại: .*syllabus\.pdf/ })).toBeInTheDocument()
  })
})

const videoSummary: api.VideoSummary = {
  id: 'video-1', course_id: 'course-1', document_id: 'doc-1', focus: 'FCFS', output_language: 'vi', model_alias: 'gemini-flash',
  voice_alias: 'voice-heygen', image_alias: 'image-nano-banana', status: 'draft', scene_count: 9, draft_version: 1,
  rendered_version: null, duration_seconds: null, error: null, published_at: null, created_at: '2026-01-01T00:00:00Z',
}

describe('CourseDetailPage - videos', () => {
  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.listModules).mockResolvedValue([])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('lists videos under their document with their status and dates only rows that share a title', async () => {
    vi.mocked(api.listVideos).mockResolvedValue([
      { ...videoSummary, id: 'video-1', focus: 'FCFS', created_at: '2026-11-13T09:05:00Z' },
      { ...videoSummary, id: 'video-2', focus: null, status: 'generating', scene_count: 0, created_at: '2026-11-14T00:00:00Z' },
      { ...videoSummary, id: 'video-3', focus: 'FCFS', status: 'rendering', created_at: '2026-11-16T00:20:15Z' },
      { ...videoSummary, id: 'video-4', focus: 'SJF', status: 'ready', rendered_version: 1, duration_seconds: 291, published_at: '2026-01-02T00:00:00Z' },
      { ...videoSummary, id: 'video-5', focus: 'RR', status: 'failed', scene_count: 0, error: 'Sinh lời thoại thất bại' },
    ])

    renderPage()

    const list = await screen.findByRole('list', { name: 'Nội dung tạo từ syllabus.pdf' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(5)
    expect(within(items[0]).getByRole('link', { name: /FCFS/ })).toHaveAttribute('href', '/videos/video-1')
    expect(within(items[0]).getByText('Video')).toBeInTheDocument()
    // A ready video says no status, as a module does; it is dated only because another row shares its title.
    expect(within(items[0]).queryByRole('status')).not.toBeInTheDocument()
    expect(items[0]).not.toHaveTextContent(/Bản nháp|cảnh/)
    expect(items[1]).toHaveTextContent('Toàn bộ tài liệu')
    expect(within(items[1]).getByText('Đang tạo…')).toHaveAttribute('data-status', 'processing')
    expect(within(items[2]).getByText('Đang dựng video…')).toHaveAttribute('data-status', 'processing')
    expect(items[3]).not.toHaveTextContent(/Đã giao|phút/)
    expect(within(items[4]).getByText('Tạo thất bại')).toHaveAttribute('data-status', 'failed')

    const times = items.map((item) => item.querySelector('time'))
    expect(times[0]).toHaveAttribute('dateTime', '2026-11-13T09:05:00Z')
    expect(times[2]).toHaveAttribute('dateTime', '2026-11-16T00:20:15Z')
    expect(times[0]?.textContent).toMatch(/^Tạo lúc \d{2}:\d{2}, \d{2}\/\d{2}$/)
    expect(times[0]?.textContent).not.toBe(times[2]?.textContent)
    expect(times[1]).toBeNull()
    expect(times[3]).toBeNull()
    expect(times[4]).toBeNull()
  })

  it('handles 409/429 errors, shows pending row while in-flight, and generates with image-qwen and focus', async () => {
    let answer!: (value: { id: string; job_id: string }) => void
    vi.mocked(api.generateVideo)
      .mockRejectedValueOnce(axiosFailure(409, 'Video này đang được sinh lời thoại, vui lòng chờ.'))
      .mockRejectedValueOnce(axiosFailure(429, 'Bạn đã dùng hết lượt sinh hôm nay.'))
      .mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      .mockResolvedValueOnce({ id: 'video-2', job_id: 'job-2' })
    vi.mocked(api.listVideos)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ ...videoSummary, status: 'generating', scene_count: 0 }])

    renderPage()

    await chooseCreate(/^video$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))
    expect(await screen.findByText('Video này đang được sinh lời thoại, vui lòng chờ.')).toBeInTheDocument()
    expect(screen.queryByText('Đang gửi yêu cầu…')).not.toBeInTheDocument()
    expect(await createItem(/^video$/i)).not.toHaveAttribute('aria-disabled')

    await chooseCreate(/^video$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))
    expect(await screen.findByText('Bạn đã dùng hết lượt sinh hôm nay.')).toBeInTheDocument()

    // The request can take seconds (live run 2: 8 s); until it returns the lecturer must see it was taken.
    await chooseCreate(/^video$/i)
    await userEvent.type(within(screen.getByRole('dialog', { name: 'Tạo video' })).getByLabelText(/nên tập trung vào nội dung gì/), 'FCFS')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))
    const list = await screen.findByRole('list', { name: 'Nội dung tạo từ syllabus.pdf' })
    const pending = within(list).getByRole('listitem')
    expect(pending).toHaveTextContent('FCFS')
    expect(pending).toHaveAttribute('aria-busy', 'true')
    expect(within(pending).getByText('Đang gửi yêu cầu…')).toHaveAttribute('data-status', 'processing')
    expect(await createItem(/^video$/i)).toHaveAttribute('aria-disabled', 'true')
    expect(api.generateVideo).toHaveBeenCalledTimes(3)

    answer({ id: 'video-1', job_id: 'job-1' })
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
    expect(screen.queryByText('Đang gửi yêu cầu…')).not.toBeInTheDocument()
    expect(await createItem(/^video$/i)).not.toHaveAttribute('aria-disabled')

    await chooseCreate(/^video$/i)
    const focusInput = within(screen.getByRole('dialog', { name: 'Tạo video' })).getByLabelText(/nên tập trung vào nội dung gì/)
    await userEvent.clear(focusInput)
    await userEvent.type(focusInput, 'FCFS')
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Vẽ hình' }), 'image-qwen')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))

    await waitFor(() => expect(api.generateVideo).toHaveBeenLastCalledWith('doc-1', {
      focus: 'FCFS', outputLanguage: 'vi', modelAlias: '', voiceAlias: undefined, imageAlias: 'image-qwen',
    }))
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
  })

  it('says the video started, not that it failed, when only the list cannot be reloaded after it', async () => {
    vi.mocked(api.generateVideo).mockResolvedValue({ id: 'video-1', job_id: 'job-1' })
    vi.mocked(api.listVideos).mockResolvedValueOnce([]).mockRejectedValue(new Error('network down'))

    renderPage()
    await chooseCreate(/^video$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))

    expect(await screen.findByText('Đã bắt đầu tạo video.')).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.queryByText(/sinh video thất bại/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Đang gửi yêu cầu…')).not.toBeInTheDocument()
  })

  it('follows a video until no job is left', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listVideos)
      .mockResolvedValueOnce([{ ...videoSummary, status: 'drawing' }])
      .mockResolvedValue([videoSummary])

    renderPage()
    expect(await screen.findByText('Đang vẽ hình…')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    await waitFor(() => expect(screen.queryByText('Đang vẽ hình…')).not.toBeInTheDocument())
  })
})

describe('CourseDetailPage - delete module', () => {
  const module: ModuleSummary = {
    id: 'module-1', title: 'Giới thiệu AI', learning_objective: null, bloom_level: null,
    component_count: 6, is_control: false,
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('asks for confirmation, handles failure, and deletes and refreshes when confirmed', async () => {
    vi.mocked(api.listModules).mockResolvedValueOnce([module]).mockResolvedValueOnce([])
    vi.mocked(api.deleteModule)
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ status: 'deleted', deleted_module_ids: ['module-1'] })
    vi.spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)
      .mockReturnValueOnce(true)

    renderPage()

    const deleteButton = await screen.findByRole('button', { name: /xóa mô-đun giới thiệu ai/i })
    await userEvent.click(deleteButton)
    expect(window.confirm).toHaveBeenCalledTimes(1)
    expect(api.deleteModule).not.toHaveBeenCalled()
    expect(screen.getByText(/giới thiệu ai/i)).toBeInTheDocument()

    await userEvent.click(deleteButton)
    expect(await screen.findByText(/xóa mô-đun thất bại/i)).toBeInTheDocument()
    expect(screen.getByText(/giới thiệu ai/i)).toBeInTheDocument()

    await userEvent.click(deleteButton)
    await waitFor(() => expect(api.deleteModule).toHaveBeenCalledWith('module-1'))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /xóa mô-đun giới thiệu ai/i })).not.toBeInTheDocument(),
    )
    expect(screen.getByText(/đã xóa mô-đun "giới thiệu ai"/i)).toBeInTheDocument()
  })

  it('says the module is deleted, and only the reload failed, when the list cannot be reloaded afterwards', async () => {
    vi.mocked(api.listModules)
      .mockResolvedValueOnce([module])
      .mockRejectedValueOnce(new Error('network down'))
    vi.mocked(api.deleteModule).mockResolvedValue({ status: 'deleted', deleted_module_ids: ['module-1'] })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /xóa mô-đun giới thiệu ai/i }))

    expect(await screen.findByText(/đã xóa mô-đun "giới thiệu ai"/i)).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.getByText(/mô-đun đã được xóa/i)).toBeInTheDocument()
    expect(screen.queryByText(/xóa mô-đun thất bại/i)).not.toBeInTheDocument()
    expect(api.deleteModule).toHaveBeenCalledTimes(1)
  })
})

describe('CourseDetailPage - delete a flashcard set or a video from its row', () => {
  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('deletes a flashcard set after confirmation and drops it from the list', async () => {
    vi.mocked(api.listFlashcardSets).mockResolvedValueOnce([flashcardSet]).mockResolvedValueOnce([])
    vi.mocked(api.deleteFlashcardSet).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Xóa flashcard Mảng' }))

    await waitFor(() => expect(api.deleteFlashcardSet).toHaveBeenCalledWith('set-1'))
    expect(await screen.findByText('Đã xóa bộ flashcard "Mảng".')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Xóa flashcard Mảng' })).not.toBeInTheDocument())
  })

  it('keeps a video when the user cancels, and says the server error when deleting fails', async () => {
    vi.mocked(api.listVideos).mockResolvedValue([videoSummary])
    vi.mocked(api.deleteVideo).mockRejectedValue(new Error('boom'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)

    renderPage()
    const button = await screen.findByRole('button', { name: /^Xóa video FCFS/ })
    await userEvent.click(button)
    expect(api.deleteVideo).not.toHaveBeenCalled()

    await userEvent.click(button)
    expect(await screen.findByText(/xóa video thất bại/i)).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: /^Xóa video FCFS/ })).toBeEnabled()
  })

  it('offers no delete while a flashcard set or a video is being made, drawn or rendered, as for a module', async () => {
    vi.mocked(api.listFlashcardSets).mockResolvedValue([{ ...flashcardSet, status: 'generating' }])
    vi.mocked(api.listVideos).mockResolvedValue([
      { ...videoSummary, id: 'v-gen', focus: 'Đang viết', status: 'generating' },
      { ...videoSummary, id: 'v-draw', focus: 'Đang vẽ', status: 'drawing' },
      { ...videoSummary, id: 'v-render', focus: 'Đang dựng', status: 'rendering' },
      { ...videoSummary, id: 'v-done', focus: 'Xong', status: 'ready' },
    ])

    renderPage()
    expect(await screen.findByRole('button', { name: 'Xóa video Xong' })).toBeInTheDocument()
    for (const name of ['Xóa flashcard Mảng', 'Xóa video Đang viết', 'Xóa video Đang vẽ', 'Xóa video Đang dựng']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })
})

describe('CourseDetailPage - filter the documents by status', () => {
  const doc = (id: string, filename: string, status: string, failures: DocumentInfo['failures'] = []): DocumentInfo => ({
    ...sampleDocument, id, filename, status, failures,
  })
  const generationFailure = {
    job_id: 'job-g', job_type: 'module_generation' as const, error: 'quota',
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listModules).mockResolvedValue([])
    vi.mocked(api.listDocuments).mockResolvedValue([
      doc('d1', 'ready.pdf', 'embedded'),
      doc('d2', 'running.pdf', 'processing'),
      doc('d3', 'broken.pdf', 'failed'),
      doc('d4', 'ready-but-generation-failed.pdf', 'embedded', [generationFailure]),
    ])
  })

  async function pick(label: string) {
    await userEvent.selectOptions(await screen.findByLabelText('Trạng thái'), label)
  }

  it('shows all by default, filters across Đang xử lý, Lỗi, and Sẵn sàng, and preserves filter on poll', async () => {
    renderPage()

    expect(await screen.findByText('ready.pdf')).toBeInTheDocument()
    expect(screen.getByText('running.pdf')).toBeInTheDocument()
    expect(screen.getByText('broken.pdf')).toBeInTheDocument()
    expect(screen.getByText('ready-but-generation-failed.pdf')).toBeInTheDocument()

    await pick('Đang xử lý')
    expect(screen.getByText('running.pdf')).toBeInTheDocument()
    expect(screen.queryByText('ready.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()

    await pick('Lỗi')
    expect(screen.getByText('broken.pdf')).toBeInTheDocument()
    expect(screen.getByText('ready-but-generation-failed.pdf')).toBeInTheDocument()
    expect(screen.queryByText('ready.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('running.pdf')).not.toBeInTheDocument()

    await pick('Sẵn sàng')
    expect(screen.getByText('ready.pdf')).toBeInTheDocument()
    expect(screen.queryByText('running.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()
  })

  it('says so when nothing has the chosen status', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([doc('d1', 'ready.pdf', 'embedded')])
    renderPage()
    await pick('Lỗi')

    expect(screen.getByText(/không có tài liệu nào ở trạng thái này/i)).toBeInTheDocument()
  })
})

describe('CourseDetailPage - failed work and retry', () => {
  const pipelineFailure = {
    job_id: 'job-p', job_type: 'document_pipeline' as const, error: 'Không đọc được tệp PDF',
  }
  const generationFailure = {
    job_id: 'job-s', job_type: 'module_generation' as const, error: 'Hết hạn mức',
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listModules).mockResolvedValue([])
  })

  it('shows pipeline, flashcard, and video script failures with retry button across documents', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([
      { ...sampleDocument, id: 'doc-1', filename: 'broken.pdf', status: 'failed', failures: [pipelineFailure] },
      {
        ...sampleDocument,
        id: 'doc-2',
        filename: 'media.pdf',
        failures: [
          { job_id: 'job-f', job_type: 'flashcard_generation', error: 'Model từ chối' },
          { job_id: 'job-v', job_type: 'video_script', error: 'Kịch bản hỏng' },
        ],
      },
    ])

    renderPage()

    expect(await screen.findByText('Xử lý tài liệu thất bại')).toBeInTheDocument()
    expect(screen.getByText('Không đọc được tệp PDF')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Thử lại: Xử lý tài liệu thất bại/i })).toBeEnabled()
    expect(screen.getByText('Tạo flashcard thất bại')).toBeInTheDocument()
    expect(screen.getByText('Viết lời thoại video thất bại')).toBeInTheDocument()
    expect(screen.queryByText('Tạo mô-đun thất bại')).not.toBeInTheDocument()
  })

  it('keeps a failed generation visible on its document after a reload', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, failures: [generationFailure] }])

    const first = renderPage()
    expect(await screen.findByText('Tạo mô-đun thất bại')).toBeInTheDocument()
    expect(screen.getByText('Hết hạn mức')).toBeInTheDocument()
    first.unmount()

    renderPage()
    expect(await screen.findByText('Tạo mô-đun thất bại')).toBeInTheDocument()
  })

  it('handles retry error, disables while retrying, and reloads the list on success', async () => {
    let resolveRetry!: (value: { id: string; status: string }) => void
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([{ ...sampleDocument, failures: [generationFailure] }])
      .mockResolvedValue([{ ...sampleDocument, generating: true, failures: [] }])
    vi.mocked(api.retryJob)
      .mockRejectedValueOnce(new Error('boom'))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve }))

    renderPage()

    const retryButton = await screen.findByRole('button', { name: /thử lại/i })
    await userEvent.click(retryButton)
    expect(await screen.findByText(/thử lại thất bại/i)).toBeInTheDocument()
    expect(screen.getByText('Hết hạn mức')).toBeInTheDocument()

    await userEvent.click(retryButton)
    expect(retryButton).toBeDisabled()

    await act(async () => resolveRetry({ id: 'job-s', status: 'queued' }))
    await waitFor(() => expect(api.retryJob).toHaveBeenCalledWith('job-s'))
    await waitFor(() => expect(screen.queryByText('Hết hạn mức')).not.toBeInTheDocument())
    expect(api.generateModule).not.toHaveBeenCalled() // the same job runs again, nothing is created
    expect(api.uploadDocument).not.toHaveBeenCalled() // and nothing is uploaded again
  })

  it('says the retry was sent, and only the reload failed, when the list cannot be reloaded afterwards', async () => {
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([{ ...sampleDocument, failures: [generationFailure] }])
      .mockRejectedValueOnce(new Error('network down'))
    vi.mocked(api.retryJob).mockResolvedValue({ id: 'job-s', status: 'queued' })

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /thử lại/i }))

    expect(await screen.findByText(/đã gửi yêu cầu thử lại\./i)).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.queryByText(/thử lại thất bại/i)).not.toBeInTheDocument()
  })

  it('does not raise a transient banner for a failed generation: the failure lives on the document', async () => {
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockResolvedValue([{ ...sampleDocument, failures: [generationFailure] }])
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })

    renderPage()
    await chooseCreate(/^mô-đun$/i)
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    expect(await screen.findByText('Tạo mô-đun thất bại')).toBeInTheDocument()
    expect(screen.getAllByText('Hết hạn mức')).toHaveLength(1)
  })
})

describe('CourseDetailPage - OCR warnings', () => {
  beforeEach(() => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
  })
  const ocrWarning = (page: number) => ({ code: 'ocr_failed', page })

  it('shows sorted OCR page warnings and unverified formula warnings per document', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([
      {
        ...sampleDocument,
        id: 'doc-1',
        filename: 'syllabus.pdf',
        extraction_warnings: [{ code: 'formula_unverified', page: 7 }, ocrWarning(2)],
      },
      {
        ...sampleDocument,
        id: 'doc-2',
        filename: 'notes.pdf',
        extraction_warnings: [ocrWarning(9), ocrWarning(2), ocrWarning(5)],
      },
    ])

    renderPage()

    expect(await screen.findByText('syllabus.pdf')).toBeInTheDocument()
    expect(screen.getByText(/Công thức có thể đọc chưa đúng ở trang 7\. Hãy soát lại công thức/)).toBeInTheDocument()
    expect(screen.getByText(/Không nhận dạng được chữ ở trang 2\. Nội dung có thể thiếu/)).toBeInTheDocument()
    expect(screen.getByText(/Không nhận dạng được chữ ở các trang 2, 5, 9\./)).toBeInTheDocument()
    expect(screen.getAllByText(/Không nhận dạng được chữ ở/)).toHaveLength(2)
  })
})

describe('CourseDetailPage - load failure', () => {
  it('says the documents could not be loaded instead of claiming there are none, and retries', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments)
      .mockRejectedValueOnce(axiosFailure(500, 'boom'))
      .mockResolvedValue([sampleDocument])
    vi.mocked(api.listModules).mockResolvedValue([])

    renderPage()

    expect(await screen.findByText('Không tải được tài liệu của môn học.')).toBeInTheDocument()
    expect(screen.queryByText(/Chưa có tài liệu/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }))

    expect(await screen.findByText('syllabus.pdf')).toBeInTheDocument()
    expect(screen.queryByText('Không tải được tài liệu của môn học.')).not.toBeInTheDocument()
  })
})
