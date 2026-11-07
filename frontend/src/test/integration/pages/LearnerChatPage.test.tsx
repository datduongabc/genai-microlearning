import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import * as AuthHookModule from '../../../auth/useAuth'

vi.mock('../../../components/CourseChat', () => ({
  CourseChat: ({ courseId }: { courseId: string }) => <p>chat of {courseId}</p>,
}))

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

describe('LearnerChatPage', () => {
  it('shows the chat of the course the launch opened, and nothing else of it', () => {
    renderAt('/learn/course-1')
    expect(screen.getByText('chat of course-1')).toBeInTheDocument()
    expect(screen.getByText('Sinh Viên A')).toBeInTheDocument()
  })

  it('asks to open the tool from the LMS without a course', () => {
    renderAt('/learn')
    expect(screen.getByRole('status')).toHaveTextContent('Moodle')
  })
})
