import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { GenerateDialog } from '../../../components/GenerateDialog'

function renderDialog(kind?: 'module' | 'flashcards' | 'video') {
  const dialogRef = createRef<HTMLDialogElement>()
  const onGenerate = vi.fn()
  render(<GenerateDialog dialogRef={dialogRef} onGenerate={onGenerate} kind={kind} />)
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

  it('asks a flashcard set for a focus, and hands over the trimmed choices', async () => {
    const { dialogRef, onGenerate } = renderDialog('flashcards')

    expect(screen.getByRole('heading', { name: 'Sinh flashcard' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Hướng dẫn thêm/)).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/Trọng tâm/), '  Mảng và danh sách liên kết ')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh flashcard' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', 'Mảng và danh sách liên kết'))
    expect(dialogRef.current!.close).toHaveBeenCalled()
  })

  it('refuses a focus over 1000 characters and names the focus in the message', async () => {
    const { onGenerate } = renderDialog('flashcards')

    fireEvent.change(screen.getByLabelText(/Trọng tâm/), { target: { value: 'a'.repeat(1001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Sinh flashcard' }))

    expect(await screen.findByText('Trọng tâm tối đa 1000 ký tự')).toBeInTheDocument()
    expect(onGenerate).not.toHaveBeenCalled()
  })

  it('names a video the same way', () => {
    renderDialog('video')

    expect(screen.getByRole('heading', { name: 'Sinh video' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Trọng tâm/)).toBeInTheDocument()
  })
})
