import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { GenerateDialog } from '../../../components/GenerateDialog'

function renderDialog() {
  const dialogRef = createRef<HTMLDialogElement>()
  const onGenerate = vi.fn()
  render(<GenerateDialog dialogRef={dialogRef} onGenerate={onGenerate} />)
  // jsdom has no <dialog> behaviour: the open attribute is what it can show.
  dialogRef.current!.setAttribute('open', '')
  dialogRef.current!.close = vi.fn()
  return { dialogRef, onGenerate }
}

describe('GenerateDialog', () => {
  it('starts with Vietnamese and no guidance, and hands over the trimmed choices', async () => {
    const { dialogRef, onGenerate } = renderDialog()

    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', ''))
    expect(dialogRef.current!.close).toHaveBeenCalled()

    await userEvent.selectOptions(screen.getByLabelText(/Ngôn ngữ đầu ra/), 'en')
    await userEvent.type(screen.getByLabelText(/Hướng dẫn thêm/), '  Explain IDS ')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenLastCalledWith('en', 'Explain IDS'))
  })

  it('refuses guidance over 1000 characters and says so', async () => {
    const { dialogRef, onGenerate } = renderDialog()

    fireEvent.change(screen.getByLabelText(/Hướng dẫn thêm/), { target: { value: 'a'.repeat(1001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Sinh mô-đun' }))

    expect(await screen.findByText('Hướng dẫn tối đa 1000 ký tự')).toBeInTheDocument()
    expect(onGenerate).not.toHaveBeenCalled()
    expect(dialogRef.current!.close).not.toHaveBeenCalled()
  })
})
