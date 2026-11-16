/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as AuthHookModule from '../../../auth/useAuth'
import * as api from '../../../api'
import { LearnFlashcardsPage } from '../../../pages/LearnFlashcardsPage'

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['apiErrorMessage', 'api'].includes(name) ? vi.fn() : value,
    ]),
  )
})

// The stylesheet as text: vitest empties a css module however it is imported.
const indexCss = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')

const set = {
  id: 'set-1', course_id: 'course-1', focus: 'Mảng',
  cards: [
    { front: 'Mảng là gì?', back: 'Dãy ô nhớ liên tiếp.', sources: ['bai-giang.pdf · trang 3'] },
    { front: 'Heap là gì?', back: 'Một cây đặc biệt.', sources: [] },
  ],
}

function renderPage() {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r',
    user: { id: 'u', email: 'e', name: 'Sinh Viên A', is_admin: false, can_author: false },
    login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
  })
  return render(
    <MemoryRouter initialEntries={['/learn/course-1/flashcards/set-1']}>
      <Routes>
        <Route path="/learn/:courseId" element={<p>Trang học</p>} />
        <Route path="/learn/:courseId/flashcards/:setId" element={<LearnFlashcardsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getLearnFlashcardSet).mockResolvedValue(set)
})

describe('LearnFlashcardsPage', () => {
  it('flips a card and moves to the next', async () => {
    renderPage()

    const card = await screen.findByRole('button', { name: /Mảng là gì\?/ })
    expect(screen.queryByText('Dãy ô nhớ liên tiếp.')).not.toBeInTheDocument()
    await userEvent.click(card)
    expect(screen.getByText('Dãy ô nhớ liên tiếp.')).toBeInTheDocument()
    expect(screen.queryByText('Mảng là gì?')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Thẻ sau' }))
    expect(screen.getByText('Thẻ 2/2')).toBeInTheDocument()
    // The next card starts on its front, not on the back left open by the last one.
    expect(screen.getByText('Heap là gì?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thẻ sau' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Thẻ trước' }))
    expect(screen.getByText('Thẻ 1/2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Thẻ trước' })).toBeDisabled()
  })

  it('flips with the space key', async () => {
    renderPage()

    const card = await screen.findByRole('button', { name: /Mảng là gì\?/ })
    card.focus()
    await userEvent.keyboard(' ')
    expect(screen.getByText('Dãy ô nhớ liên tiếp.')).toBeInTheDocument()
    await userEvent.keyboard(' ')
    expect(screen.getByText('Mảng là gì?')).toBeInTheDocument()
  })

  it('shows the source label on the back', async () => {
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /Mảng là gì\?/ }))
    expect(screen.getByText('Nguồn: bai-giang.pdf · trang 3')).toBeInTheDocument()
  })

  it('shows an error and a way back when the set cannot be loaded', async () => {
    vi.mocked(api.getLearnFlashcardSet).mockRejectedValue(new Error('x'))

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được bộ thẻ')
    expect(screen.getByRole('link', { name: 'Về trang học' })).toHaveAttribute('href', '/learn/course-1')
  })

  it('works at 390 px wide', async () => {
    renderPage()

    const card = await screen.findByRole('button', { name: /Mảng là gì\?/ })
    // jsdom has no layout, so what can be checked is that nothing is given a width that a phone cannot fit.
    // That the page does not scroll sideways at 390 px is checked in a real browser.
    expect(card).toHaveClass('learn-card')
    expect(card.getAttribute('style')).toBeNull()
    const rule = /\.learn-card\s*\{([^}]*)\}/.exec(indexCss)?.[1] ?? ''
    expect(rule).toMatch(/max-width:\s*100%|width:\s*100%/)
    expect(rule).toMatch(/overflow-wrap:\s*anywhere/)
    expect(rule).not.toMatch(/(?<![-\w])(min-)?width:\s*\d+px/)
  })
})
