import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
  listModuleConcepts: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(videos.getMediaModels).mockReset().mockResolvedValue(mediaModels)
  vi.mocked(api.listModels).mockReset().mockResolvedValue({
    models: [{ alias: 'deepseek-flash', label: 'DeepSeek V4.1 Flash', provider: 'deepseek', note: null }], default: null,
  })
  vi.mocked(api.listModuleConcepts).mockReset()
})

const concepts = {
  vi: [{ name: 'Bảng băm', summary: 'Cấu trúc lưu cặp khóa và giá trị.' }, { name: 'Xung đột', summary: 'Hai khóa cùng một chỉ số.' }],
  en: [{ name: 'Hash table', summary: 'Stores key and value pairs.' }],
}

describe('GenerateDialog for a module of a document', () => {
  function renderModule() {
    const onGenerate = vi.fn()
    render(<GenerateDialog open onOpenChange={vi.fn()} onGenerate={onGenerate} kind="module" documentId="doc-1" />)
    return onGenerate
  }

  it('lists the concepts, most important first and picked, and hands over the one picked', async () => {
    vi.mocked(api.listModuleConcepts).mockImplementation(async (_doc, language) => concepts[language])
    const onGenerate = renderModule()

    const select = await screen.findByRole('combobox', { name: 'Khái niệm của mô-đun' })
    await waitFor(() => expect(select).toHaveValue('Bảng băm'))
    expect(screen.getByRole('option', { name: 'Bảng băm (quan trọng nhất)' })).toBeInTheDocument()
    expect(screen.getByText('Cấu trúc lưu cặp khóa và giá trị.')).toBeInTheDocument()

    await userEvent.selectOptions(select, 'Xung đột')
    expect(screen.getByText('Hai khóa cùng một chỉ số.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '', concept: concepts.vi[1] }))
  })

  it('asks again only for another language, not while the guidance is typed', async () => {
    vi.mocked(api.listModuleConcepts).mockImplementation(async (_doc, language) => concepts[language])
    renderModule()

    await screen.findByRole('option', { name: 'Bảng băm (quan trọng nhất)' })
    await userEvent.type(screen.getByLabelText(/Yêu cầu thêm cho mô-đun/), 'Thêm ví dụ')
    await userEvent.selectOptions(screen.getByLabelText(/^Ngôn ngữ đầu ra$/), 'en')
    await screen.findByRole('option', { name: 'Hash table (quan trọng nhất)' })
    await userEvent.selectOptions(screen.getByLabelText(/^Ngôn ngữ đầu ra$/), 'vi')
    await screen.findByRole('option', { name: 'Bảng băm (quan trọng nhất)' })

    expect(vi.mocked(api.listModuleConcepts).mock.calls).toEqual([['doc-1', 'vi'], ['doc-1', 'en']])
  })

  it('still generates, leaving the concept to the server, when the list cannot be had', async () => {
    vi.mocked(api.listModuleConcepts).mockRejectedValue(new Error('timeout'))
    const onGenerate = renderModule()

    expect(await screen.findByText(/Chưa lấy được danh sách khái niệm/)).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Khái niệm của mô-đun' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '' }))
  })

  it('can generate while the list is still on its way', async () => {
    vi.mocked(api.listModuleConcepts).mockReturnValue(new Promise(() => {}))
    const onGenerate = renderModule()

    expect(await screen.findByRole('option', { name: /Đang tìm các khái niệm/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '' }))
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
    await userEvent.type(screen.getByLabelText(/Yêu cầu thêm cho mô-đun/), '  Explain IDS ')
    // The model is picked in the dialog, under the guidance.
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Mô hình' }), 'deepseek-flash')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    await waitFor(() => expect(onGenerate).toHaveBeenLastCalledWith('en', 'Explain IDS', { modelAlias: 'deepseek-flash' }))
  })

  it('refuses guidance over 1000 characters and says so', async () => {
    const { onOpenChange, onGenerate } = renderDialog()

    fireEvent.change(screen.getByLabelText(/Yêu cầu thêm cho mô-đun/), { target: { value: 'a'.repeat(1001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Tạo mô-đun' }))

    expect(await screen.findByText('Yêu cầu thêm tối đa 1000 ký tự')).toBeInTheDocument()
    expect(onGenerate).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('asks a flashcard set for a focus, and hands over the trimmed choices', async () => {
    const { onOpenChange, onGenerate } = renderDialog('flashcards')

    expect(screen.getByRole('heading', { name: 'Tạo flashcard' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/Yêu cầu thêm cho mô-đun/)).not.toBeInTheDocument()
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

  it('asks a video for its voice and image model, defaults to undefined aliases, and sends selected choices', async () => {
    const { onGenerate } = renderDialog('video')

    const labels = screen.getAllByRole('combobox').map((select) => select.getAttribute('aria-label') ?? select.closest('label')?.firstChild?.textContent?.trim())
    expect(labels).toEqual(['Ngôn ngữ đầu ra', 'Mô hình', 'Giọng đọc', 'Vẽ hình'])
    expect(screen.getByRole('heading', { name: 'Tạo video' })).toBeInTheDocument()
    expect(screen.getByLabelText('Video nên tập trung vào nội dung gì?')).toBeInTheDocument()

    const voice = screen.getByLabelText('Giọng đọc')
    const image = screen.getByLabelText('Vẽ hình')
    // The system's default first, as for the text model, then the server's choices.
    await waitFor(() => expect(screen.getByRole('option', { name: 'HeyGen Voice 1 Turbo' })).toBeInTheDocument())
    expect(voice).toHaveValue('')
    expect(image).toHaveValue('')
    expect(within(voice).getAllByRole('option')[0]).toHaveTextContent('Mặc định của hệ thống')
    expect(within(image).getAllByRole('option')[0]).toHaveTextContent('Mặc định của hệ thống')
    expect(screen.getByRole('option', { name: 'Qwen Image 2.1 Turbo' })).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith('vi', '', { modelAlias: '', voiceAlias: undefined, imageAlias: undefined }))

    await userEvent.selectOptions(voice, 'voice-gemini-lite')
    await userEvent.selectOptions(image, 'image-qwen')
    await userEvent.type(screen.getByLabelText(/nên tập trung vào nội dung gì/), 'BFS')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo video' }))

    await waitFor(() => expect(onGenerate).toHaveBeenLastCalledWith('vi', 'BFS', { modelAlias: '', voiceAlias: 'voice-gemini-lite', imageAlias: 'image-qwen' }))
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
