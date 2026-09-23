import { render, screen, waitFor } from '@testing-library/react'
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
