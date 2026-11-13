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

const linkedCourse: Course = { ...unlinkedCourse, moodle_course_id: 6 }

const sampleDocument: DocumentInfo = {
  id: 'doc-1',
  filename: 'syllabus.pdf',
  status: 'embedded',
  created_at: '2026-01-01T00:00:00Z',
  module_count: 0,
  generating: false,
  failures: [],
  extraction_warnings: [],
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
  // The course chat loads its last conversation on open.
  vi.mocked(api.getLatestCourseChat).mockResolvedValue({ session_id: null, messages: [] })
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

  it('offers authorized Moodle courses in a drop-down when the course is not linked to Moodle', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])

    renderPage()

    expect(await screen.findByText(/chưa liên kết với moodle/i)).toBeInTheDocument()
    const select = await screen.findByRole('combobox', { name: /course moodle/i })
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    expect(within(select).getByRole('option', { name: 'Databases (DB)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /tạo course moodle/i })).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/tìm course moodle/i)).not.toBeInTheDocument()
  })

  it('lets the teacher choose a Moodle course from the drop-down and link it', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([unlinkedCourse])
      .mockResolvedValueOnce([{ ...unlinkedCourse, moodle_course_id: 6 }])
    vi.mocked(api.linkMoodleCourse).mockResolvedValue({ moodle_course_id: 6 })

    renderPage()

    const select = await screen.findByRole('combobox', { name: /course moodle/i })
    expect(screen.getByRole('button', { name: 'Liên kết' })).toBeDisabled()
    await screen.findByRole('option', { name: /Intro2AI/ })
    await userEvent.selectOptions(select, '6')
    await userEvent.click(screen.getByRole('button', { name: 'Liên kết' }))

    await waitFor(() => {
      expect(api.linkMoodleCourse).toHaveBeenCalledWith('course-1', 6)
    })
    expect(await screen.findByText(/đã liên kết với moodle course id/i)).toBeInTheDocument()
  })

  it('asks for a course to be chosen instead of linking nothing', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])

    renderPage()
    const select = await screen.findByRole('combobox', { name: /course moodle/i })
    await screen.findByRole('option', { name: /Intro2AI/ })
    fireEvent.submit(select.closest('form')!)

    expect(await screen.findByText('Vui lòng chọn một course Moodle')).toBeInTheDocument()
    expect(api.linkMoodleCourse).not.toHaveBeenCalled()
  })

  it('says what the server said when a Moodle course cannot be linked', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.linkMoodleCourse).mockRejectedValue(axiosFailure(404, 'Không tìm thấy khóa học Moodle này'))

    renderPage()
    await screen.findByRole('option', { name: /Intro2AI/ })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /course moodle/i }), '6')
    await userEvent.click(screen.getByRole('button', { name: 'Liên kết' }))

    expect(await screen.findByText('Không tìm thấy khóa học Moodle này')).toBeInTheDocument()
  })

  it('says so when the list of Moodle courses cannot be loaded', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listMoodleCourses).mockRejectedValue(axiosFailure(502, 'Moodle không phản hồi'))

    renderPage()

    expect(await screen.findByText('Moodle không phản hồi')).toBeInTheDocument()
  })

  it('offers no Moodle course until the account is linked', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })

    renderPage()

    const account = await screen.findByRole('region', { name: 'Tài khoản Moodle' })
    expect(await within(account).findByText('Chưa liên kết')).toBeInTheDocument()
    expect(within(account).queryByLabelText('Mật khẩu Moodle')).not.toBeInTheDocument()
    expect(await within(account).findByLabelText('Email Moodle')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /tạo course moodle/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /course moodle/i })).not.toBeInTheDocument()
    expect(api.listMoodleCourses).not.toHaveBeenCalled()
  })

  it('shows the linked course id and no drop-down once linked', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([{ ...unlinkedCourse, moodle_course_id: 6 }])

    renderPage()

    expect(await screen.findByText(/đã liên kết với moodle course id/i)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /course moodle/i })).not.toBeInTheDocument()
  })

  it('always shows the Moodle account in the linked-course view too, with nothing to fill in', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([linkedCourse])

    renderPage()

    const account = await screen.findByRole('region', { name: 'Tài khoản Moodle' })
    expect(await within(account).findByText('Đã liên kết')).toBeInTheDocument()
    expect(within(account).queryByRole('button', { name: 'Hủy liên kết Moodle' })).not.toBeInTheDocument()
    expect(within(account).queryByLabelText('Mật khẩu Moodle')).not.toBeInTheDocument()
  })

  it('flips the account to not linked when the server says the link must be made again', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([linkedCourse])

    renderPage()
    const account = await screen.findByRole('region', { name: 'Tài khoản Moodle' })
    expect(await within(account).findByText('Đã liên kết')).toBeInTheDocument()

    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    act(() => {
      window.dispatchEvent(new Event(api.MOODLE_ACCOUNT_CHANGED_EVENT))
    })

    expect(await within(account).findByText('Chưa liên kết')).toBeInTheDocument()
    expect(within(account).getByText(/nhập email moodle của bạn/i)).toBeInTheDocument()
  })


})

describe('CourseDetailPage - unlink Moodle', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('asks for confirmation and does not unlink when the user cancels', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([linkedCourse])
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /hủy liên kết/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(api.unlinkMoodleCourse).not.toHaveBeenCalled()
    expect(screen.getByText(/đã liên kết với moodle course id/i)).toBeInTheDocument()
  })

  it('unlinks and reflects the unlinked state when confirmed', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([linkedCourse])
      .mockResolvedValueOnce([unlinkedCourse])
    vi.mocked(api.unlinkMoodleCourse).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /hủy liên kết/i }))

    await waitFor(() => {
      expect(api.unlinkMoodleCourse).toHaveBeenCalledWith('course-1')
    })
    expect(await screen.findByText(/chưa liên kết với moodle/i)).toBeInTheDocument()
  })

  it('keeps the not-linked line in place while the account answers, with no "link first" flash', async () => {
    let answer!: (value: { email: string; status: 'linked' }) => void
    vi.mocked(api.getMoodleAccount).mockReturnValue(new Promise((resolve) => { answer = resolve }))
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    renderPage()
    const line = await screen.findByText('Môn học này chưa liên kết với Moodle.')
    expect(screen.queryByText(/liên kết tài khoản moodle trước/i)).not.toBeInTheDocument()

    await act(async () => answer({ email: 'lan@x.edu', status: 'linked' }))

    expect(await screen.findByRole('combobox', { name: /course moodle/i })).toBeInTheDocument()
    expect(line).toBeInTheDocument()
  })

  it('asks for the account link first only where Moodle exists', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    const { unmount } = renderPage()
    expect(await screen.findByText('Liên kết tài khoản Moodle trước để chọn khóa học.')).toBeInTheDocument()
    unmount()

    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'unavailable' })
    renderPage()
    expect(await screen.findByText(/mở từ moodle \(lti\) vẫn dùng bình thường/i)).toBeInTheDocument()
    expect(screen.getByText('Môn học này chưa liên kết với Moodle.')).toBeInTheDocument()
    expect(screen.queryByText(/liên kết tài khoản moodle trước/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /course moodle/i })).not.toBeInTheDocument()
  })
})

describe('CourseDetailPage - delete document', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('asks for confirmation and does not delete when the user cancels', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /^xóa$/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(api.deleteDocument).not.toHaveBeenCalled()
    expect(screen.getByText('syllabus.pdf')).toBeInTheDocument()
  })

  it('deletes the document and refreshes the list when confirmed', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockResolvedValueOnce([])
    vi.mocked(api.deleteDocument).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /^xóa$/i }))

    await waitFor(() => {
      expect(api.deleteDocument).toHaveBeenCalledWith('doc-1')
    })
    await waitFor(() => {
      expect(screen.queryByText('syllabus.pdf')).not.toBeInTheDocument()
    })
  })

  it('says the document is deleted, and only the reload failed, when the list cannot be reloaded afterwards', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockRejectedValueOnce(new Error('network down'))
    vi.mocked(api.deleteDocument).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^xóa$/i }))

    expect(await screen.findByText(/đã xóa tài liệu "syllabus.pdf"/i)).toBeInTheDocument()
    expect(await screen.findByText(/không tải lại được danh sách/i)).toBeInTheDocument()
    expect(screen.getByText(/tài liệu đã được xóa/i)).toBeInTheDocument()
    expect(screen.queryByText(/xóa tài liệu thất bại/i)).not.toBeInTheDocument()
  })

  it('shows the server error when deleting the document fails', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.deleteDocument).mockRejectedValue(new Error('boom'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /^xóa$/i }))

    expect(await screen.findByText(/xóa tài liệu thất bại/i)).toBeInTheDocument()
    expect(screen.getByText('syllabus.pdf')).toBeInTheDocument()
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

  it.each(['uploaded', 'processing'])(
    'stays disabled and busy when the server reports a %s document, e.g. after a page refresh',
    async (status) => {
      vi.mocked(api.listDocuments).mockResolvedValue([{ ...processingDocument, status }])

      renderPage()

      expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()
      expect(uploadInput()).toBeDisabled()
    },
  )

  it('is enabled when every document is ready or failed', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([
      sampleDocument,
      { ...sampleDocument, id: 'doc-3', status: 'failed' },
    ])

    renderPage()

    expect(await screen.findByText('Tải tài liệu lên')).toBeInTheDocument()
    expect(uploadInput()).toBeEnabled()
  })

  it('is disabled until the first document list has loaded', async () => {
    let resolveDocuments!: (docs: DocumentInfo[]) => void
    vi.mocked(api.listDocuments).mockReturnValue(
      new Promise<DocumentInfo[]>((resolve) => {
        resolveDocuments = resolve
      }),
    )

    renderPage()

    expect(uploadInput()).toBeDisabled()
    await act(async () => resolveDocuments([]))
    await waitFor(() => expect(uploadInput()).toBeEnabled())
  })

  it('becomes available again by itself once polling sees the document finish', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([processingDocument])
      .mockResolvedValue([{ ...processingDocument, status: 'embedded' }])

    renderPage()
    expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    await waitFor(() => expect(uploadInput()).toBeEnabled())
  })

  it('stays disabled after a successful upload until the server reports the document done', async () => {
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ ...processingDocument, status: 'uploaded' }])
    vi.mocked(api.uploadDocument).mockResolvedValue({ id: 'doc-2', status: 'uploaded', job_id: 'job-1' })

    renderPage()
    await screen.findByText('Tải tài liệu lên')
    await userEvent.upload(uploadInput(), new File(['x'], 'big.pdf', { type: 'application/pdf' }))

    expect(await screen.findByText(/đang xử lý tài liệu/i)).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()
  })

  it("shows the server's explanation when it refuses the upload with 409", async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([])
    vi.mocked(api.uploadDocument).mockRejectedValue(
      Object.assign(new Error('Request failed with status code 409'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: 'Đang có tài liệu khác được xử lý' } },
      }),
    )

    renderPage()
    await screen.findByText('Tải tài liệu lên')
    await userEvent.upload(uploadInput(), new File(['x'], 'big.pdf', { type: 'application/pdf' }))

    expect(await screen.findByText('Đang có tài liệu khác được xử lý')).toBeInTheDocument()
  })
})

describe('CourseDetailPage - upload form', () => {
  function uploadInput() {
    return document.querySelector('input[type="file"]') as HTMLInputElement
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
  })

  it('sends the picked file to the server', async () => {
    vi.mocked(api.uploadDocument).mockResolvedValue({ id: 'doc-2', status: 'uploaded', job_id: 'job-1' })
    const file = new File(['x'], 'lecture.pdf', { type: 'application/pdf' })

    renderPage()
    await screen.findByText('Tải tài liệu lên')
    await userEvent.upload(uploadInput(), file)

    await waitFor(() => expect(api.uploadDocument).toHaveBeenCalledWith('course-1', file, expect.any(Function)))
  })

  it('shows how much of the file has been sent while it is being sent', async () => {
    vi.mocked(api.uploadDocument).mockImplementation(async (_course, _file, onProgress) => {
      onProgress?.(0.42)
      return new Promise(() => {}) // still sending
    })

    renderPage()
    await screen.findByText('Tải tài liệu lên')
    await userEvent.upload(uploadInput(), new File(['x'], 'lecture.mp4', { type: 'video/mp4' }))

    expect(await screen.findByText('Đang tải lên 42%')).toBeInTheDocument()
    expect(uploadInput()).toBeDisabled()
  })

  it('says a file is too big instead of sending it', async () => {
    const file = new File(['x'], 'lecture.pdf', { type: 'application/pdf' })
    Object.defineProperty(file, 'size', { value: 25 * 1024 * 1024 + 1 })

    renderPage()
    await screen.findByText('Tải tài liệu lên')
    await userEvent.upload(uploadInput(), file)

    expect(await screen.findByText('Tài liệu tối đa 25 MB')).toBeInTheDocument()
    expect(api.uploadDocument).not.toHaveBeenCalled()
    expect(uploadInput()).toBeEnabled()
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

  it('offers to generate a module when nothing is in progress', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])

    renderPage()

    expect(await screen.findByRole('button', { name: /\+ sinh mô-đun/i })).toBeEnabled()
  })

  it('cancels the dialog without starting generation', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Huỷ' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.generateModule).not.toHaveBeenCalled()
  })

  it('shows the button busy and disabled when the server reports a generation in progress, e.g. after a refresh', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([generatingDocument])

    renderPage()

    expect(await screen.findByRole('button', { name: /đang sinh mô-đun/i })).toBeDisabled()
  })

  it('becomes available again and shows the new module once polling sees the generation finish', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([generatingDocument])
      .mockResolvedValue([{ ...sampleDocument, module_count: 1 }])
    vi.mocked(api.listModules).mockResolvedValueOnce([]).mockResolvedValue([newModule])

    renderPage()
    expect(await screen.findByRole('button', { name: /đang sinh mô-đun/i })).toBeDisabled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await screen.findByRole('button', { name: /\+ sinh mô-đun/i })).toBeEnabled()
    expect(await screen.findByText(/Intro to search/)).toBeInTheDocument()
  })

  it('generates with the teacher saved default model preselected', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.listModels).mockResolvedValue({
      models: [{ alias: 'groq-pro', label: 'GPT-OSS 120B qua Groq', provider: 'groq', note: null }],
      default: 'groq-pro',
    })
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    vi.mocked(api.pollJobStatus).mockReturnValue(new Promise(() => {}))

    renderPage()
    await waitFor(() =>
      expect((screen.getByRole('combobox', { name: /model sinh/i }) as HTMLSelectElement).value).toBe('groq-pro'),
    )
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    expect(api.generateModule).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: /ngôn ngữ đầu ra/i }), 'en')
    await userEvent.type(screen.getByLabelText(/Hướng dẫn thêm/), 'Giải thích kỹ DLDFS và IDS')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    expect(api.generateModule).toHaveBeenCalledWith('doc-1', 'groq-pro', 'en', 'Giải thích kỹ DLDFS và IDS')
  })

  it('shows why a chosen model cannot be used instead of a generic failure', async () => {
    // Backend thử model trước khi xếp hàng và trả 422 kèm lý do, ví dụ khóa trống.
    // Giảng viên phải đọc được đúng lý do đó để biết mà chọn model khác.
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = {
      status: 422,
      data: { detail: "Model 'openai-fast' chưa dùng được: chưa có API key hợp lệ. Hãy chọn model khác." },
    }
    vi.mocked(api.generateModule).mockRejectedValue(
      new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse),
    )

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    expect(await screen.findByText(/chưa có API key hợp lệ/)).toBeInTheDocument()
  })

  it('starts a generation and shows it as busy while the job runs', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    vi.mocked(api.pollJobStatus).mockReturnValue(new Promise(() => {}))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    expect(api.generateModule).toHaveBeenCalledWith('doc-1', '', 'vi', '')
    expect(await screen.findByRole('button', { name: /đang sinh mô-đun/i })).toBeDisabled()
  })
})

const flashcardSet: api.FlashcardSetSummary = {
  id: 'set-1', course_id: 'course-1', document_id: 'doc-1', focus: 'Mảng', output_language: 'vi', model_alias: 'gemini-fast',
  status: 'draft', card_count: 8, published_at: null, created_at: '2026-01-01T00:00:00Z',
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

    const list = await screen.findByRole('list', { name: 'Flashcard sinh từ syllabus.pdf' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(4)
    expect(within(items[0]).getByRole('link', { name: /Mảng/ })).toHaveAttribute('href', '/flashcard-sets/set-1')
    expect(items[0]).toHaveTextContent('Bản nháp - 8 thẻ')
    // A set and a video with the same focus must not look alike.
    expect(within(items[0]).getByText('Flashcard')).toBeInTheDocument()
    expect(items[1]).toHaveTextContent('Toàn bộ tài liệu')
    expect(items[1]).toHaveTextContent('Đang sinh...')
    expect(items[2]).toHaveTextContent('Sinh thất bại')
    expect(items[3]).toHaveTextContent('Đã giao - 8 thẻ')
    // A draft is neutral, a published set is done, and "processing" means generating only.
    expect(within(items[0]).getByText('Bản nháp - 8 thẻ')).not.toHaveAttribute('data-status')
    expect(within(items[1]).getByText('Đang sinh...')).toHaveAttribute('data-status', 'processing')
    expect(within(items[3]).getByText('Đã giao - 8 thẻ')).toHaveAttribute('data-status', 'embedded')
    const other = screen.getByRole('list', { name: 'Flashcard sinh từ other.pdf' })
    expect(within(other).getAllByRole('listitem')).toHaveLength(1)
  })

  it('generates a set with the focus and the chosen model, then shows it as generating', async () => {
    vi.mocked(api.generateFlashcards).mockResolvedValue({ id: 'set-1', job_id: 'job-1' })
    vi.mocked(api.listFlashcardSets)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ ...flashcardSet, status: 'generating', card_count: 0 }])

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '+ Sinh flashcard' }))
    // Each kind has its own dialog in the page, so the field is looked up in the open one.
    await userEvent.type(within(screen.getByRole('dialog', { name: 'Sinh flashcard' })).getByLabelText(/Trọng tâm/), 'Mảng')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh flashcard' }))

    expect(api.generateFlashcards).toHaveBeenCalledWith('doc-1', { focus: 'Mảng', outputLanguage: 'vi', modelAlias: '' })
    expect(await screen.findByText('Đang sinh...')).toBeInTheDocument()
  })

  it('says an identical set is already being generated when the server answers 409', async () => {
    vi.mocked(api.generateFlashcards).mockRejectedValue(axiosFailure(409, 'Bộ thẻ này đang được sinh, vui lòng chờ.'))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '+ Sinh flashcard' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh flashcard' }))

    expect(await screen.findByText('Bộ thẻ này đang được sinh, vui lòng chờ.')).toBeInTheDocument()
  })

  it('follows a generating set until the server reports it done', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listFlashcardSets)
      .mockResolvedValueOnce([{ ...flashcardSet, status: 'generating', card_count: 0 }])
      .mockResolvedValue([flashcardSet])

    renderPage()
    expect(await screen.findByText('Đang sinh...')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await screen.findByText('Bản nháp - 8 thẻ')).toBeInTheDocument()
  })
})

const videoSummary: api.VideoSummary = {
  id: 'video-1', course_id: 'course-1', document_id: 'doc-1', focus: 'FCFS', output_language: 'vi', model_alias: 'gemini-fast',
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

  it('lists videos under their document with their status', async () => {
    vi.mocked(api.listVideos).mockResolvedValue([
      videoSummary,
      { ...videoSummary, id: 'video-2', focus: null, status: 'generating', scene_count: 0 },
      { ...videoSummary, id: 'video-3', status: 'rendering' },
      { ...videoSummary, id: 'video-4', status: 'ready', rendered_version: 1, duration_seconds: 291, published_at: '2026-01-02T00:00:00Z' },
      { ...videoSummary, id: 'video-5', status: 'failed', scene_count: 0, error: 'Sinh lời thoại thất bại' },
    ])

    renderPage()

    const list = await screen.findByRole('list', { name: 'Video sinh từ syllabus.pdf' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(5)
    expect(within(items[0]).getByRole('link', { name: /FCFS/ })).toHaveAttribute('href', '/videos/video-1')
    expect(within(items[0]).getByText('Video')).toBeInTheDocument()
    expect(within(items[0]).getByText('Bản nháp - 9 cảnh')).not.toHaveAttribute('data-status')
    expect(items[1]).toHaveTextContent('Toàn bộ tài liệu')
    expect(within(items[1]).getByText('Đang sinh lời thoại...')).toHaveAttribute('data-status', 'processing')
    expect(within(items[2]).getByText('Đang dựng video...')).toHaveAttribute('data-status', 'processing')
    expect(within(items[3]).getByText('Đã giao - 9 cảnh, 4,9 phút')).toHaveAttribute('data-status', 'embedded')
    expect(within(items[4]).getByText('Sinh thất bại')).toHaveAttribute('data-status', 'failed')
  })

  it('generates a video with the focus, the model, the voice and the image model', async () => {
    vi.mocked(api.generateVideo).mockResolvedValue({ id: 'video-1', job_id: 'job-1' })
    vi.mocked(api.listVideos)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ ...videoSummary, status: 'generating', scene_count: 0 }])

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '+ Sinh video' }))
    await userEvent.type(within(screen.getByRole('dialog', { name: 'Sinh video' })).getByLabelText(/Trọng tâm/), 'FCFS')
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Model vẽ hình' }), 'image-qwen')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh video' }))

    await waitFor(() => expect(api.generateVideo).toHaveBeenCalledWith('doc-1', {
      focus: 'FCFS', outputLanguage: 'vi', modelAlias: '', voiceAlias: 'voice-heygen', imageAlias: 'image-qwen',
    }))
    expect(await screen.findByText('Đang sinh lời thoại...')).toBeInTheDocument()
  })

  it('shows the server message when the generation quota is used up', async () => {
    vi.mocked(api.generateVideo).mockRejectedValue(axiosFailure(429, 'Bạn đã dùng hết lượt sinh hôm nay.'))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '+ Sinh video' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh video' }))

    expect(await screen.findByText('Bạn đã dùng hết lượt sinh hôm nay.')).toBeInTheDocument()
  })

  it('follows a video until no job is left', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.mocked(api.listVideos)
      .mockResolvedValueOnce([{ ...videoSummary, status: 'drawing' }])
      .mockResolvedValue([videoSummary])

    renderPage()
    expect(await screen.findByText('Đang vẽ hình...')).toBeInTheDocument()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })

    expect(await screen.findByText('Bản nháp - 9 cảnh')).toBeInTheDocument()
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

  it('asks for confirmation and keeps the module when the user cancels', async () => {
    vi.mocked(api.listModules).mockResolvedValue([module])
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /xóa mô-đun giới thiệu ai/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(api.deleteModule).not.toHaveBeenCalled()
    expect(screen.getByText(/giới thiệu ai/i)).toBeInTheDocument()
  })

  it('deletes the module and refreshes the list when confirmed', async () => {
    vi.mocked(api.listModules).mockResolvedValueOnce([module]).mockResolvedValueOnce([])
    vi.mocked(api.deleteModule).mockResolvedValue({ status: 'deleted', deleted_module_ids: ['module-1'] })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /xóa mô-đun giới thiệu ai/i }))

    await waitFor(() => expect(api.deleteModule).toHaveBeenCalledWith('module-1'))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /xóa mô-đun giới thiệu ai/i })).not.toBeInTheDocument(),
    )
    expect(screen.getByText(/đã xóa mô-đun "giới thiệu ai"/i)).toBeInTheDocument()
  })

  it('shows the server error and keeps the module when deleting fails', async () => {
    vi.mocked(api.listModules).mockResolvedValue([module])
    vi.mocked(api.deleteModule).mockRejectedValue(new Error('boom'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /xóa mô-đun giới thiệu ai/i }))

    expect(await screen.findByText(/xóa mô-đun thất bại/i)).toBeInTheDocument()
    expect(screen.getByText(/giới thiệu ai/i)).toBeInTheDocument()
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
    await userEvent.selectOptions(await screen.findByLabelText('Trạng thái:'), label)
  }

  it('shows every document by default', async () => {
    renderPage()

    expect(await screen.findByText('ready.pdf')).toBeInTheDocument()
    expect(screen.getByText('running.pdf')).toBeInTheDocument()
    expect(screen.getByText('broken.pdf')).toBeInTheDocument()
    expect(screen.getByText('ready-but-generation-failed.pdf')).toBeInTheDocument()
  })

  it('narrows to the documents being processed', async () => {
    renderPage()
    await pick('Đang xử lý')

    expect(screen.getByText('running.pdf')).toBeInTheDocument()
    expect(screen.queryByText('ready.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()
  })

  it('narrows to the ready documents', async () => {
    renderPage()
    await pick('Sẵn sàng')

    expect(screen.getByText('ready.pdf')).toBeInTheDocument()
    expect(screen.queryByText('running.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()
  })

  it('narrows to what failed, including a ready document whose generation failed', async () => {
    renderPage()
    await pick('Lỗi')

    expect(screen.getByText('broken.pdf')).toBeInTheDocument()
    expect(screen.getByText('ready-but-generation-failed.pdf')).toBeInTheDocument()
    expect(screen.queryByText('ready.pdf')).not.toBeInTheDocument()
    expect(screen.queryByText('running.pdf')).not.toBeInTheDocument()
  })

  it('says so when nothing has the chosen status', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([doc('d1', 'ready.pdf', 'embedded')])
    renderPage()
    await pick('Lỗi')

    expect(screen.getByText(/không có tài liệu nào ở trạng thái này/i)).toBeInTheDocument()
  })

  it('keeps the filter when the list is polled again', async () => {
    renderPage()
    await pick('Sẵn sàng')

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })

    expect(screen.queryByText('broken.pdf')).not.toBeInTheDocument()
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

  it('shows a failed document with its error and a retry button', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, status: 'failed', failures: [pipelineFailure] }])

    renderPage()

    expect(await screen.findByText('Xử lý tài liệu thất bại')).toBeInTheDocument()
    expect(screen.getByText('Không đọc được tệp PDF')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /thử lại/i })).toBeEnabled()
  })

  it('keeps a failed generation visible on its document after a reload', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, failures: [generationFailure] }])

    const first = renderPage()
    expect(await screen.findByText('Sinh mô-đun thất bại')).toBeInTheDocument()
    expect(screen.getByText('Hết hạn mức')).toBeInTheDocument()
    first.unmount()

    renderPage()
    expect(await screen.findByText('Sinh mô-đun thất bại')).toBeInTheDocument()
  })

  it('retries the failed job from the lecturer screen and reloads the list', async () => {
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([{ ...sampleDocument, failures: [generationFailure] }])
      .mockResolvedValue([{ ...sampleDocument, generating: true, failures: [] }])
    vi.mocked(api.retryJob).mockResolvedValue({ id: 'job-s', status: 'queued' })

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /thử lại/i }))

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

  it('says why a retry was refused and keeps the failure on screen', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, failures: [generationFailure] }])
    vi.mocked(api.retryJob).mockRejectedValue(new Error('boom'))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /thử lại/i }))

    expect(await screen.findByText(/thử lại thất bại/i)).toBeInTheDocument()
    expect(screen.getByText('Hết hạn mức')).toBeInTheDocument()
  })

  it('does not raise a transient banner for a failed generation: the failure lives on the document', async () => {
    vi.mocked(api.listDocuments)
      .mockResolvedValueOnce([sampleDocument])
      .mockResolvedValue([{ ...sampleDocument, failures: [generationFailure] }])
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    vi.mocked(api.pollJobStatus).mockResolvedValue({
      id: 'job-1', step: 'generate', status: 'failed', attempts: 5, last_error: 'Hết hạn mức',
    })

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    expect(await screen.findByText('Sinh mô-đun thất bại')).toBeInTheDocument()
    expect(screen.getAllByText('Hết hạn mức')).toHaveLength(1)
  })
})

describe('CourseDetailPage - OCR warnings', () => {
  beforeEach(() => {
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
  })
  const ocrWarning = (page: number) => ({ code: 'ocr_failed', page })

  it('keeps an OCR page warning on an embedded document after remounting', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...sampleDocument, extraction_warnings: [ocrWarning(2)] }])
    const first = renderPage()
    expect(await screen.findByText(/OCR không đọc được trang 2\./)).toBeInTheDocument()
    expect(screen.getByText('syllabus.pdf')).toBeInTheDocument()
    first.unmount()
    renderPage()
    expect(await screen.findByText(/OCR không đọc được trang 2\./)).toBeInTheDocument()
  })

  it('lists every failed page of a document on one warning line', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([{
      ...sampleDocument, extraction_warnings: [ocrWarning(9), ocrWarning(2), ocrWarning(5)],
    }])
    renderPage()
    expect(await screen.findByText(/OCR không đọc được các trang 2, 5, 9\./)).toBeInTheDocument()
    expect(screen.getAllByText(/OCR không đọc được/)).toHaveLength(1)
  })

  it('asks to check the formulas of pages whose formulas could not be re-read', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.listDocuments).mockResolvedValue([{
      ...sampleDocument, extraction_warnings: [{ code: 'formula_unverified', page: 7 }, ocrWarning(2)],
    }])
    renderPage()
    expect(await screen.findByText(/Công thức có thể đọc chưa đúng ở trang 7\. Hãy soát lại công thức/))
      .toBeInTheDocument()
    expect(screen.getByText(/OCR không đọc được trang 2\. Nội dung có thể thiếu/)).toBeInTheDocument()
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
