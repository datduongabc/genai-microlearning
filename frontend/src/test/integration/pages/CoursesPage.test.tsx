import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CoursesPage } from '../../../pages/CoursesPage'
import { AuthProvider } from '../../../auth/AuthContext'
import * as api from '../../../api'
import type { Course } from '../../../api'

vi.mock('../../../api')

const course: Course = {
  id: 'course-1',
  name: 'AI 101',
  created_at: '2026-01-01T00:00:00Z',
  moodle_course_id: null,
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <CoursesPage />
      </MemoryRouter>
    </AuthProvider>,
  )
}

// From the keyboard, as in CourseDetailPage.test: in jsdom a Radix trigger can stop opening on a mouse press.
async function chooseDelete() {
  ;(await screen.findByRole('button', { name: 'Thao tác khác với AI 101' })).focus()
  await userEvent.keyboard('{Enter}')
  await userEvent.click(await screen.findByRole('menuitem', { name: 'Xóa môn học' }))
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('CoursesPage - delete', () => {
  it('asks for confirmation and does not delete when the user cancels', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([course])
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()

    await chooseDelete()

    expect(window.confirm).toHaveBeenCalled()
    expect(api.deleteCourse).not.toHaveBeenCalled()
    expect(screen.getByText('AI 101')).toBeInTheDocument()
  })

  it('deletes the course and refreshes the list when confirmed', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([course])
      .mockResolvedValueOnce([])
    vi.mocked(api.deleteCourse).mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await chooseDelete()

    await waitFor(() => {
      expect(api.deleteCourse).toHaveBeenCalledWith('course-1')
    })
    await waitFor(() => {
      expect(screen.queryByText('AI 101')).not.toBeInTheDocument()
    })
  })

  it('says why the course was not deleted, and keeps it in the list', async () => {
    const refusal = 'Tài liệu đang được xử lý hoặc đang sinh nội dung, vui lòng chờ xong rồi xóa.'
    vi.mocked(api.listCourses).mockResolvedValue([course])
    vi.mocked(api.deleteCourse).mockRejectedValue(new Error('409'))
    vi.mocked(api.apiErrorMessage).mockReturnValue(refusal)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<><AuthProvider><MemoryRouter><CoursesPage /></MemoryRouter></AuthProvider></>)
    await chooseDelete()

    expect(await screen.findByText(refusal)).toBeInTheDocument()
    expect(api.apiErrorMessage).toHaveBeenCalledWith(expect.any(Error), 'Xóa môn học thất bại')
    expect(screen.getByText('AI 101')).toBeInTheDocument()
  })
})

describe('CoursesPage - rows', () => {
  it('names the Moodle course only for a linked course, and keeps delete out of the row', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([course, { ...course, id: 'course-2', name: 'OS', moodle_course_id: 5 }])

    renderPage()

    expect(await screen.findByText('Đã liên kết khóa học Moodle #5')).toBeInTheDocument()
    expect(screen.queryByText(/chưa liên kết/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^xóa/i })).not.toBeInTheDocument()
  })
})

describe('CoursesPage - create', () => {
  it('creates the course with the trimmed name, empties the field and reloads the list', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([course])
    vi.mocked(api.createCourse).mockResolvedValue(course)

    renderPage()

    const field = await screen.findByLabelText('Tên môn học mới')
    await userEvent.type(field, '  AI 101  ')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo môn học' }))

    await waitFor(() => expect(api.createCourse).toHaveBeenCalledWith('AI 101'))
    expect(await screen.findByText('AI 101')).toBeInTheDocument()
    expect(field).toHaveValue('')
  })

  it('asks for a name instead of sending a blank one', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([])

    renderPage()

    await userEvent.type(await screen.findByLabelText('Tên môn học mới'), '   ')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo môn học' }))

    expect(await screen.findByText('Vui lòng nhập tên môn học')).toBeInTheDocument()
    expect(api.createCourse).not.toHaveBeenCalled()
  })
})

describe('CoursesPage - load failure', () => {
  it('says the list could not be loaded instead of claiming there are no courses, and retries', async () => {
    vi.mocked(api.listCourses)
      .mockRejectedValueOnce(new Error('500'))
      .mockResolvedValueOnce([course])

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được danh sách môn học.')
    expect(screen.queryByText(/Chưa có môn học/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Thử lại' }))

    expect(await screen.findByText('AI 101')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('CoursesPage - rename', () => {
  async function chooseRename() {
    ;(await screen.findByRole('button', { name: 'Thao tác khác với AI 101' })).focus()
    await userEvent.keyboard('{Enter}')
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Đổi tên' }))
  }

  it('renames from the menu and shows the new name', async () => {
    vi.mocked(api.listCourses)
      .mockResolvedValueOnce([course])
      .mockResolvedValue([{ ...course, name: 'Hệ điều hành' }])
    vi.mocked(api.renameCourse).mockResolvedValue({ ...course, name: 'Hệ điều hành' })
    renderPage()
    await chooseRename()

    const field = screen.getByRole('textbox', { name: 'Tên môn học' })
    await userEvent.clear(field)
    await userEvent.type(field, 'Hệ điều hành')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))

    await waitFor(() => expect(api.renameCourse).toHaveBeenCalledWith('course-1', 'Hệ điều hành'))
    expect(await screen.findByRole('link', { name: 'Hệ điều hành' })).toBeInTheDocument()
  })

  it('keeps the name and sends nothing when cancelled or empty', async () => {
    vi.mocked(api.listCourses).mockResolvedValue([course])
    renderPage()
    await chooseRename()

    await userEvent.clear(screen.getByRole('textbox', { name: 'Tên môn học' }))
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    expect(await screen.findByText('Vui lòng nhập tên môn học')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))

    expect(screen.getByRole('link', { name: 'AI 101' })).toBeInTheDocument()
    expect(api.renameCourse).not.toHaveBeenCalled()
  })
})
