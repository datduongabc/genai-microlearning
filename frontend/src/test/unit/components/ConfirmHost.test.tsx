import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConfirmHost } from '../../../components/ConfirmHost'
import { confirmAction } from '../../../lib/confirm'

function ask() {
  let answer!: Promise<boolean>
  act(() => { answer = confirmAction({ title: 'Xóa tài liệu "a.pdf"?', description: 'Không thể khôi phục.', action: 'Xóa tài liệu', destructive: true }) })
  return answer
}

describe('ConfirmHost', () => {
  it('shows the question and answers yes from the action button', async () => {
    render(<ConfirmHost />)
    const answer = ask()
    const dialog = await screen.findByRole('alertdialog', { name: 'Xóa tài liệu "a.pdf"?' })
    expect(dialog).toHaveTextContent('Không thể khôi phục.')
    await userEvent.click(screen.getByRole('button', { name: 'Xóa tài liệu' }))
    await expect(answer).resolves.toBe(true)
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('answers no from Cancel and gives focus back to what asked', async () => {
    render(<><button>Xóa</button><ConfirmHost /></>)
    screen.getByRole('button', { name: 'Xóa' }).focus()
    const answer = ask()
    await userEvent.click(await screen.findByRole('button', { name: 'Hủy' }))
    await expect(answer).resolves.toBe(false)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Xóa' })).toHaveFocus())
  })

  it('answers no from Escape, with focus starting on Cancel so Enter cannot delete by accident', async () => {
    render(<ConfirmHost />)
    const answer = ask()
    await screen.findByRole('alertdialog')
    expect(screen.getByRole('button', { name: 'Hủy' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    await expect(answer).resolves.toBe(false)
  })
})
