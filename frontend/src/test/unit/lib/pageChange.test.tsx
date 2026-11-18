import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, Link, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { useDocumentTitle } from '../../../lib/documentTitle'
import { PageChange } from '../../../lib/pageChange'

function Courses() {
  useDocumentTitle('Môn học')
  return <main><h1>Môn học</h1><Link to="/courses/c1">Hệ điều hành</Link></main>
}

function Course() {
  useDocumentTitle('Hệ điều hành')
  return <main><h1>Hệ điều hành</h1><button type="button">Tải tài liệu lên</button></main>
}

describe('PageChange', () => {
  it('gives the focus to the new page heading and names the tab after the page', async () => {
    const router = createMemoryRouter([{ element: <PageChange />, children: [
      { path: '/courses', element: <Courses /> },
      { path: '/courses/c1', element: <Course /> },
    ] }], { initialEntries: ['/courses'] })
    render(<RouterProvider router={router} />)

    // The first page keeps the browser's focus: nothing has moved yet.
    expect(screen.getByRole('heading', { name: 'Môn học' })).not.toHaveFocus()
    expect(document.title).toBe('Môn học - GENAILMS')

    screen.getByRole('link', { name: 'Hệ điều hành' }).focus()
    await userEvent.keyboard('{Enter}')

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Hệ điều hành' })).toHaveFocus())
    expect(document.title).toBe('Hệ điều hành - GENAILMS')
  })
})
