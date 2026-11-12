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

    await userEvent.click(await screen.findByRole('button', { name: /xóa/i }))

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

    await userEvent.click(await screen.findByRole('button', { name: /xóa/i }))

    await waitFor(() => {
      expect(api.deleteCourse).toHaveBeenCalledWith('course-1')
    })
    await waitFor(() => {
      expect(screen.queryByText('AI 101')).not.toBeInTheDocument()
    })
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
