import { render, screen, waitFor } from '@testing-library/react'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LtiLaunchPage } from '../../../pages/LtiLaunchPage'
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
      </Routes>
    </MemoryRouter>,
  )
}

describe('LtiLaunchPage', () => {
  it('trades the code once, wipes it from the address bar and opens the course', async () => {
    loginWithLaunchCode.mockResolvedValue('course-1')

    renderAt('#code=abc123&course=course-1')

    await waitFor(() => expect(screen.getByText('course page')).toBeInTheDocument())
    expect(loginWithLaunchCode).toHaveBeenCalledTimes(1)
    expect(loginWithLaunchCode).toHaveBeenCalledWith('abc123')
    expect(window.location.hash).toBe('')
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
})
