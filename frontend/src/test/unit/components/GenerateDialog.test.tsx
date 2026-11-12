import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createRef } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GenerateDialog } from '../../../components/GenerateDialog'
import * as videos from '../../../api/videos'

vi.mock('../../../api/videos', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/videos')>()),
  getMediaModels: vi.fn(),
}))

const mediaModels = {
  voices: [
    { alias: 'voice-heygen', label: 'HeyGen Voice 1 Turbo', price: 'khoảng $0,03 một video 8 phút' },
    { alias: 'voice-gemini-lite', label: 'Gemini 3.8 Flash-Lite TTS', price: 'khoảng $0,07 một video 8 phút' },
    { alias: 'voice-gemini-flash', label: 'Gemini 3.8 Flash TTS', price: 'khoảng $0,11 một video 8 phút' },
  ],
  images: [
    { alias: 'image-nano-banana', label: 'Gemini Nano Banana 2.1', price: 'khoảng $0,037 một ảnh' },
    { alias: 'image-qwen', label: 'Qwen Image 2.1 Turbo', price: 'khoảng $0,018 một ảnh' },
  ],
  default_voice: 'voice-heygen',
  default_image: 'image-nano-banana',
}

beforeEach(() => {
  vi.mocked(videos.getMediaModels).mockReset().mockResolvedValue(mediaModels)
})

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

  it('asks a video for its voice and image model, HeyGen and Nano Banana first, each with its price', async () => {
    const { onGenerate } = renderDialog('video')

    const voice = screen.getByLabelText('Giọng đọc')
    const image = screen.getByLabelText('Model vẽ hình')
    // The choices are the server's.
    await waitFor(() => expect(voice).toHaveValue('voice-heygen'))
    expect(image).toHaveValue('image-nano-banana')
    expect(screen.getByRole('option', { name: /HeyGen Voice 1 Turbo.*khoảng \$0,03 một video 8 phút/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Gemini 3.8 Flash TTS.*khoảng \$0,11/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /Qwen Image 2.1 Turbo.*khoảng \$0,018 một ảnh/ })).toBeInTheDocument()

    await userEvent.selectOptions(voice, 'voice-gemini-lite')
    await userEvent.selectOptions(image, 'image-qwen')
    await userEvent.type(screen.getByLabelText(/Trọng tâm/), 'BFS')
    await userEvent.click(screen.getByRole('button', { name: 'Sinh video' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', 'BFS', { voiceAlias: 'voice-gemini-lite', imageAlias: 'image-qwen' }))
  })

  it('offers no voice or image model to a module or a flashcard set, and asks the server for none', () => {
    renderDialog('module')
    expect(screen.queryByLabelText('Giọng đọc')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Model vẽ hình')).not.toBeInTheDocument()
    expect(videos.getMediaModels).not.toHaveBeenCalled()
  })

  it('leaves the voice and image model to the server when their list cannot be loaded', async () => {
    vi.mocked(videos.getMediaModels).mockRejectedValue(new Error('offline'))
    const { onGenerate } = renderDialog('video')

    expect(await screen.findByText(/Không tải được danh sách giọng và model ảnh/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Sinh video' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { voiceAlias: undefined, imageAlias: undefined }))
  })
})
