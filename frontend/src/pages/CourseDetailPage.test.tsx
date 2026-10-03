import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CourseDetailPage } from './CourseDetailPage'
import { AuthProvider } from '../auth/AuthContext'
import * as api from '../api'
import type { Course, DocumentInfo, ModuleSummary } from '../api'

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
  kind: 'lecture',
  section_count: 0,
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
  vi.mocked(api.listSections).mockResolvedValue([])
  // Ô chọn model gọi API này ngay khi trang mở, như listDocuments ở trên.
  vi.mocked(api.listModels).mockResolvedValue({ models: [], default: null })
})

describe('CourseDetailPage - Moodle linking', () => {
  it('shows a search box when the course is not linked to Moodle', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])

    renderPage()

    expect(await screen.findByText(/chưa liên kết với moodle/i)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/tìm course moodle/i)).toBeInTheDocument()
  })

  it('lets the teacher search and link an existing Moodle course', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([unlinkedCourse])
      .mockResolvedValueOnce([{ ...unlinkedCourse, moodle_course_id: 6 }])
    vi.mocked(api.searchMoodleCourses).mockResolvedValue([
      { id: 6, fullname: 'Introduction to Artificial Intelligence', shortname: 'Intro2AI' },
    ])
    vi.mocked(api.linkMoodleCourse).mockResolvedValue({ moodle_course_id: 6 })

    renderPage()

    await userEvent.type(await screen.findByPlaceholderText(/tìm course moodle/i), 'Artificial')
    await userEvent.click(screen.getByRole('button', { name: 'Tìm' }))

    expect(await screen.findByText(/Introduction to Artificial Intelligence/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /liên kết/i }))

    await waitFor(() => {
      expect(api.linkMoodleCourse).toHaveBeenCalledWith('course-1', 6)
    })
    expect(await screen.findByText(/đã liên kết với moodle course id/i)).toBeInTheDocument()
  })

  it('says what the server said when a Moodle course cannot be linked', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
    vi.mocked(api.searchMoodleCourses).mockResolvedValue([{ id: 6, fullname: 'Intro to AI', shortname: 'AI' }])
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = { status: 404, statusText: '', data: { detail: 'Không tìm thấy khóa học Moodle này' }, headers: {}, config }
    vi.mocked(api.linkMoodleCourse).mockRejectedValue(new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response as AxiosResponse))

    renderPage()
    await userEvent.type(await screen.findByPlaceholderText(/tìm course moodle/i), 'Intro')
    await userEvent.click(screen.getByRole('button', { name: 'Tìm' }))
    await userEvent.click(await screen.findByRole('button', { name: /liên kết/i }))

    expect(await screen.findByText('Không tìm thấy khóa học Moodle này')).toBeInTheDocument()
  })

  it('shows the linked course id and no search box once linked', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([{ ...unlinkedCourse, moodle_course_id: 6 }])

    renderPage()

    expect(await screen.findByText(/đã liên kết với moodle course id/i)).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/tìm course moodle/i)).not.toBeInTheDocument()
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

describe('CourseDetailPage - module generation availability', () => {
  const generatingDocument: DocumentInfo = { ...sampleDocument, generating: true }
  const newModule: ModuleSummary = {
    id: 'module-1',
    title: 'Intro to search',
    learning_objective: null,
    bloom_level: null,
    is_control: false, component_count: 6,
    section_id: null,
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
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

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
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

    expect(await screen.findByText(/chưa có API key hợp lệ/)).toBeInTheDocument()
  })

  it('starts a generation and shows it as busy while the job runs', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])
    vi.mocked(api.generateModule).mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    vi.mocked(api.pollJobStatus).mockReturnValue(new Promise(() => {}))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /\+ sinh mô-đun/i }))
    await userEvent.click(screen.getByRole('button', { name: 'Sinh' }))

    expect(api.generateModule).toHaveBeenCalledWith('doc-1', '', 'vi', '')
    expect(await screen.findByRole('button', { name: /đang sinh mô-đun/i })).toBeDisabled()
  })
})

describe('CourseDetailPage - textbooks', () => {
  const textbook: DocumentInfo = { ...sampleDocument, id: 'book-1', filename: 'dsa.pdf', kind: 'textbook', section_count: 3 }

  function uploadInput() {
    return document.querySelector('input[type="file"]') as HTMLInputElement
  }

  beforeEach(() => {
    vi.mocked(api.listCourses).mockResolvedValue([unlinkedCourse])
  })

  it('uploads with the default kind and offers no kind select', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([])
    vi.mocked(api.uploadDocument).mockResolvedValue({ id: 'doc-2', status: 'uploaded', job_id: 'job-1' })
    renderPage()
    await waitFor(() => expect(uploadInput()).toBeEnabled())
    expect(screen.queryByLabelText(/loại tài liệu/i)).not.toBeInTheDocument()
    await userEvent.upload(uploadInput(), new File(['x'], 'dsa.pdf', { type: 'application/pdf' }))
    await waitFor(() => expect(api.uploadDocument).toHaveBeenCalledWith('course-1', expect.any(File)))
  })

  it('offers the outline instead of whole-document generation for a textbook with sections', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([textbook])

    renderPage()

    expect(await screen.findByRole('button', { name: /mục lục \(3\)/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /\+ sinh mô-đun/i })).not.toBeInTheDocument()
  })

  it('opens and closes the outline tree when the teacher asks for it', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([textbook])
    vi.mocked(api.listSections).mockResolvedValue([
      { id: 's1', parent_id: null, title: 'Sorting', level: 1, page_start: 1, page_end: 9, module_count: 0, generating: false },
    ])

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: /mục lục \(3\)/i }))

    expect(await screen.findByRole('checkbox', { name: /Sorting/ })).toBeInTheDocument()
    expect(api.listSections).toHaveBeenCalledWith('book-1')

    await userEvent.click(screen.getByRole('button', { name: /ẩn mục lục/i }))

    expect(screen.queryByRole('checkbox', { name: /Sorting/ })).not.toBeInTheDocument()
  })

  it('does not list a module twice while the outline shows it under its section', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([textbook])
    vi.mocked(api.listModules).mockResolvedValue([
      { id: 'm1', title: 'Merge sort', learning_objective: null, bloom_level: null, is_control: true, component_count: 6, section_id: 's1' },
    ])
    vi.mocked(api.listSections).mockResolvedValue([
      { id: 's1', parent_id: null, title: 'Sorting', level: 1, page_start: 1, page_end: 9, module_count: 1, generating: false },
    ])

    renderPage()
    expect(await screen.findAllByRole('link', { name: /Merge sort/ })).toHaveLength(1)
    expect(screen.getByText('Đối chứng (không RAG) - chỉ dùng cho nghiên cứu')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /mục lục \(3\)/i }))

    await screen.findByRole('checkbox', { name: /Sorting/ })
    expect(screen.getAllByRole('link', { name: /Merge sort/ })).toHaveLength(1)
  })

  it('explains when no outline could be detected and keeps whole-document generation', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([{ ...textbook, section_count: 0 }])

    renderPage()

    expect(await screen.findByText(/không nhận diện được mục lục/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /\+ sinh mô-đun/i })).toBeEnabled()
  })

  it('leaves a lecture exactly as before', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([sampleDocument])

    renderPage()

    expect(await screen.findByRole('button', { name: /\+ sinh mô-đun/i })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /mục lục/i })).not.toBeInTheDocument()
    expect(screen.queryByText(/không nhận diện được mục lục/i)).not.toBeInTheDocument()
  })
})

describe('CourseDetailPage - delete module', () => {
  const module: ModuleSummary = {
    id: 'module-1', title: 'Giới thiệu AI', learning_objective: null, bloom_level: null,
    component_count: 6, section_id: null, is_control: false,
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
    await waitFor(() => expect(screen.queryByText(/giới thiệu ai/i)).not.toBeInTheDocument())
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
})
