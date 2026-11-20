import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GenerateDialog } from '../../../components/GenerateDialog'
import * as videos from '../../../api/videos'
import * as api from '../../../api'

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

vi.mock('../../../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api')>()),
  listModels: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(videos.getMediaModels).mockReset().mockResolvedValue(mediaModels)
  vi.mocked(api.listModels).mockReset().mockResolvedValue({
    models: [{ alias: 'deepseek-flash', label: 'DeepSeek V4.1 Flash', provider: 'deepseek', note: null }], default: null,
  })
})

// Kept open by the test (the page closes it through onOpenChange), so a second submit can follow.
function renderDialog(kind?: 'module' | 'flashcards' | 'video') {
  const onGenerate = vi.fn()
  const onOpenChange = vi.fn()
  render(<GenerateDialog open onOpenChange={onOpenChange} onGenerate={onGenerate} kind={kind} />)
  return { onOpenChange, onGenerate }
}

describe('GenerateDialog', () => {
  it('starts with Vietnamese and no guidance, and hands over the trimmed choices', async () => {
    const { onOpenChange, onGenerate } = renderDialog()

    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)

    await userEvent.selectOptions(screen.getByLabelText(/^Ngôn ngữ đầu ra$/), 'en')
    await userEvent.type(screen.getByLabelText(/Chủ đề mà bạn muốn tập trung vào/), '  Explain IDS ')
    // The model is picked in the dialog, under the guidance.
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Mô hình' }), 'deepseek-flash')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenLastCalledWith('en', 'Explain IDS', { modelAlias: 'deepseek-flash' }))
  })

  it('refuses guidance over 1000 characters and says so', async () => {
    const { onOpenChange, onGenerate } = renderDialog()

    fireEvent.change(screen.getByLabelText(/Chủ đề mà bạn muốn tập trung vào/), { target: { value: 'a'.repeat(1001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    expect(await screen.findByText('Yêu cầu thêm tối đa 1000 ký tự')).toBeInTheDocument()
    expect(onGenerate).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('asks a flashcard set for a focus, and hands over the trimmed choices', async () => {
    const { onOpenChange, onGenerate } = renderDialog('flashcards')

    expect(screen.getByRole('heading', { name: 'Tạo flashcard' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chủ đề mà bạn muốn tập trung vào/)).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Flashcard nên tập trung vào nội dung gì?'), '  Ví dụ: các định nghĩa trong chương 2 ')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', 'Ví dụ: các định nghĩa trong chương 2', { modelAlias: '' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('refuses a focus over 1000 characters and says so', async () => {
    const { onGenerate } = renderDialog('flashcards')

    fireEvent.change(screen.getByLabelText(/nên tập trung vào nội dung gì/), { target: { value: 'a'.repeat(1001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Tạo flashcard' }))

    expect(await screen.findByText('Tối đa 1000 ký tự')).toBeInTheDocument()
    expect(onGenerate).not.toHaveBeenCalled()
  })

  it('names a video the same way, the model above the voice and the picture model', () => {
    renderDialog('video')
    const labels = screen.getAllByRole('combobox').map((select) => select.getAttribute('aria-label') ?? select.closest('label')?.firstChild?.textContent?.trim())
    expect(labels).toEqual(['Ngôn ngữ đầu ra', 'Mô hình', 'Giọng đọc', 'Vẽ hình'])

    expect(screen.getByRole('heading', { name: 'Tạo video' })).toBeInTheDocument()
    expect(screen.getByLabelText('Video nên tập trung vào nội dung gì?')).toBeInTheDocument()
  })

  it('asks a video for its voice and image model, HeyGen and Nano Banana first, with no price', async () => {
    const { onGenerate } = renderDialog('video')

    const voice = screen.getByLabelText('Giọng đọc')
    const image = screen.getByLabelText('Vẽ hình')
    // The choices are the server's.
    await waitFor(() => expect(voice).toHaveValue('voice-heygen'))
    expect(image).toHaveValue('image-nano-banana')
    expect(screen.getByRole('option', { name: 'HeyGen Voice 1 Turbo' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Qwen Image 2.1 Turbo' })).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()

    await userEvent.selectOptions(voice, 'voice-gemini-lite')
    await userEvent.selectOptions(image, 'image-qwen')
    await userEvent.type(screen.getByLabelText(/nên tập trung vào nội dung gì/), 'BFS')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', 'BFS', { modelAlias: '', voiceAlias: 'voice-gemini-lite', imageAlias: 'image-qwen' }))
  })

  it('offers no voice or image model to a module or a flashcard set, and asks the server for none', () => {
    renderDialog('module')
    expect(screen.queryByLabelText('Giọng đọc')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Vẽ hình')).not.toBeInTheDocument()
    expect(videos.getMediaModels).not.toHaveBeenCalled()
  })

  it('leaves the voice and image model to the server when their list cannot be loaded', async () => {
    vi.mocked(videos.getMediaModels).mockRejectedValue(new Error('offline'))
    const { onGenerate } = renderDialog('video')

    expect(await screen.findByText(/Không tải được danh sách giọng đọc và mô hình vẽ hình/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '', voiceAlias: undefined, imageAlias: undefined }))
  })
})
