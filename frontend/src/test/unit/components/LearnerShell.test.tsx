import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LearnerShell } from '../../../components/LearnerShell'
import * as AuthHookModule from '../../../auth/useAuth'

describe('LearnerShell', () => {
  it('lets a keyboard user skip the header straight to the page content', async () => {
    vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
      token: 't', refreshToken: 'r', user: { id: '1', email: 's@b.com', name: 'Sinh viên', is_admin: false },
      login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
    })
    render(<LearnerShell><p>Nội dung</p></LearnerShell>)

    await userEvent.tab()
    const skip = screen.getByRole('link', { name: 'Đến nội dung chính' })
    expect(skip).toHaveFocus()
    expect(skip).toHaveAttribute('href', `#${screen.getByRole('main').id}`)
  })
})
