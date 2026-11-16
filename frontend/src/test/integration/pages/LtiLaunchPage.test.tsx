import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import {
  createBrowserRouter, createRoutesFromElements, MemoryRouter, Route, RouterProvider, Routes, useNavigate,
} from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LtiLaunchPage } from '../../../pages/LtiLaunchPage'
import { useLeaveGuard } from '../../../lib/useLeaveGuard'
import * as AuthHookModule from '../../../auth/useAuth'

const loginWithLaunchCode = vi.fn()

beforeEach(() => {
  loginWithLaunchCode.mockReset()
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: null, refreshToken: null, user: null,
    login: vi.fn(), loginWithLaunchCode, logout: vi.fn(),
  })
})

function renderAt(hash: string) {
  window.history.replaceState(null, '', `/lti/launch${hash}`)
  return render(
    <MemoryRouter initialEntries={['/lti/launch']}>
      <Routes>
        <Route path="/lti/launch" element={<LtiLaunchPage />} />
        <Route path="/courses/:courseId" element={<p>course page</p>} />
        <Route path="/learn/:courseId" element={<p>learner chat</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LtiLaunchPage', () => {
  it('trades the code once, wipes it from the address bar and opens the course', async () => {
    loginWithLaunchCode.mockResolvedValue({ courseId: 'course-1', owner: true })

    renderAt('#code=abc123&course=course-1')

    await waitFor(() => expect(screen.getByText('course page')).toBeInTheDocument())
    expect(loginWithLaunchCode).toHaveBeenCalledTimes(1)
    expect(loginWithLaunchCode).toHaveBeenCalledWith('abc123')
    expect(window.location.hash).toBe('')
  })

  it('opens only the course chat when the launch made the user a member', async () => {
    loginWithLaunchCode.mockResolvedValue({ courseId: 'course-1', owner: false })

    renderAt('#code=abc123')

    await waitFor(() => expect(screen.getByText('learner chat')).toBeInTheDocument())
  })

  it('shows what the server said when the code is refused', async () => {
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = { status: 401, statusText: '', data: { detail: 'Phiên mở công cụ đã hết hạn.' }, headers: {}, config } as AxiosResponse
    loginWithLaunchCode.mockRejectedValue(new AxiosError('x', 'ERR_BAD_REQUEST', config, null, response))

    renderAt('#code=old')

    expect(await screen.findByRole('alert')).toHaveTextContent('Phiên mở công cụ đã hết hạn.')
  })

  it('asks to launch again when there is no code', async () => {
    renderAt('')

    expect(await screen.findByRole('alert')).toHaveTextContent('Hãy mở lại hoạt động từ LMS')
    expect(loginWithLaunchCode).not.toHaveBeenCalled()
  })

  // The wipe must keep the router's own entry in history.state: without its index, Back to the
  // launch entry is a move the router cannot place, and it lets an editor with edits go unasked.
  it('still asks before Back leaves an editor with unsaved edits, after the launch', async () => {
    loginWithLaunchCode.mockResolvedValue({ courseId: 'course-1', owner: true })
    window.history.replaceState(null, '', '/lti/launch#code=abc123')
    const Course = () => {
      const navigate = useNavigate()
      return <button onClick={() => navigate('/editor')}>open editor</button>
    }
    const Editor = () => {
      useLeaveGuard(true)
      return <p>editor</p>
    }
    const router = createBrowserRouter(createRoutesFromElements(
      <>
        <Route path="/lti/launch" element={<LtiLaunchPage />} />
        <Route path="/courses/:courseId" element={<Course />} />
        <Route path="/editor" element={<Editor />} />
      </>,
    ))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<RouterProvider router={router} />)

    await userEvent.click(await screen.findByText('open editor'))
    expect(await screen.findByText('editor')).toBeInTheDocument()
    await act(() => router.navigate(-1))

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByText('editor')).toBeInTheDocument()
    router.dispose()
  })
})
