import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CoursesPage } from './CoursesPage'
import { AuthProvider } from '../auth/AuthContext'
import * as api from '../api'
import type { Course } from '../api'

vi.mock('../api')

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
