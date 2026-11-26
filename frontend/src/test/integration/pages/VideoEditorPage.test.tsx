import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { createMemoryRouter, createRoutesFromElements, Route, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VideoEditorPage } from '../../../pages/VideoEditorPage'
import { AuthProvider } from '../../../auth/AuthContext'
import { ProtectedRoute } from '../../../auth/ProtectedRoute'
import * as api from '../../../api'
import type { VideoDetail, VideoScene } from '../../../api'

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['apiErrorMessage', 'api'].includes(name) ? vi.fn() : value,
    ]),
  )
})

const source = { chunk_id: 'c1', locator: 'Chapter 5.pdf · trang 9', content: 'FCFS phục vụ tiến trình theo thứ tự đến.' }

const scene = (n: number, extra: Partial<VideoScene> = {}): VideoScene => ({
  kind: 'summary', title: `Cảnh ${n}`, bullets: [`Ý một của cảnh ${n}`, `Ý hai của cảnh ${n}`],
  bullet_citations: [['c1'], ['c1']], formula: null, code: null, table: null, diagram: null, image_prompt: null,
  narration: `Câu một của cảnh ${n}.\nCâu hai của cảnh ${n}.`, narration_citations: [['c1'], ['c1']],
  citations: ['c1'], author: 'model', flags: [], has_image: false, image_outdated: false, needs_image: false,
  sources: [source], ...extra,
})

const video = (scenes: VideoScene[], extra: Partial<VideoDetail> = {}): VideoDetail => ({
  id: 'video-1', course_id: 'course-1', document_id: 'doc-1', focus: 'FCFS', output_language: 'vi',
  model_alias: 'gemini-flash', voice_alias: 'voice-heygen', image_alias: 'image-nano-banana', status: 'draft',
  scene_count: scenes.length, draft_version: 1, rendered_version: null, duration_seconds: null, error: null,
  published_at: null, created_at: '2026-11-12T00:00:00Z', scenes, estimated_seconds: 60, max_seconds: 480,
  render_problems: [], render_warnings: [], job: null, captions: [], ...extra,
})

// A data router, as in main.tsx: the leave guard needs one for useBlocker. The other pages are
// stand-ins, to tell where a navigation went.
function renderPage() {
  const router = createMemoryRouter(createRoutesFromElements(
    <>
      <Route path="/login" element={<p>Trang đăng nhập</p>} />
      <Route path="/courses" element={<p>Danh sách môn học</p>} />
      <Route path="/courses/:courseId" element={<p>Trang môn học</p>} />
      <Route element={<ProtectedRoute />}>
        <Route path="/videos/:id" element={<VideoEditorPage />} />
      </Route>
    </>,
  ), { initialEntries: ['/courses', '/videos/video-1'], initialIndex: 1 })
  return { router, ...render(<AuthProvider><RouterProvider router={router} /></AuthProvider>) }
}

const axiosFailure = (status: number, detail: string) => {
  const config = { headers: {} } as InternalAxiosRequestConfig
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null,
    { status, statusText: '', data: { detail }, headers: {}, config } as AxiosResponse)
}

let objectUrls = 0
beforeEach(() => {
  localStorage.setItem('auth', JSON.stringify({
    token: 't', refreshToken: 'r', user: { id: 'u1', email: 'lan@x.edu', name: 'Lan', can_author: true, is_admin: false },
  }))
  vi.clearAllMocks()
  objectUrls = 0
  // jsdom has no object URLs: each blob gets a distinct name so a test can tell slides apart.
  URL.createObjectURL = vi.fn(() => `blob:preview-${++objectUrls}`)
  URL.revokeObjectURL = vi.fn()
  vi.mocked(api.getScenePreview).mockImplementation(async () => new Blob(['png'], { type: 'image/png' }))
  vi.mocked(api.getMediaModels).mockResolvedValue({
    voices: [{ alias: 'voice-heygen', label: 'HeyGen Voice 1 Turbo', price: 'khoảng $0,03 một video 8 phút' }],
    images: [{ alias: 'image-nano-banana', label: 'Gemini Nano Banana 2.1', price: 'khoảng $0,037 một ảnh' }],
    default_voice: 'voice-heygen', default_image: 'image-nano-banana',
  })
})

// Fake timers that still let time pass, so the queries' own waits work. Time is moved in small
// steps: React runs the effects a step starts only once that step's act() ends.
async function passTime(ms: number, step = 300) {
  for (let spent = 0; spent < ms; spent += step) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(step)
    })
  }
}

afterEach(() => {
  vi.useRealTimers()
  localStorage.clear()
  vi.restoreAllMocks()
})

// A scene's own "Sửa": the editor opens one scene at a time, as the module editor opens one part.
const openScene = async (n: number) => userEvent.click(await screen.findByRole('button', { name: `Sửa cảnh ${n}` }))

const sceneItems = async () => within(await screen.findByRole('list', { name: 'Các cảnh' })).getAllByRole('listitem', { name: /^Cảnh \d+$/ })

describe('VideoEditorPage', () => {
  it('shows each scene, locks revise/publish while editing, cancels edits, and rewrites the draft from a request', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1), scene(2)]))
    vi.mocked(api.reviseVideo).mockResolvedValue({
      ...video([scene(1), scene(2), scene(3, { title: 'Cảnh thêm' })], { draft_version: 2 }), reply: 'Đã thêm một cảnh.', changed: true,
    })

    renderPage()

    const scenes = await sceneItems()
    expect(scenes).toHaveLength(2)
    const slide = await within(scenes[0]).findByRole('img', { name: 'Slide cảnh 1' })
    expect(slide.getAttribute('src')).toMatch(/^blob:preview-/)
    expect(within(scenes[0]).getByText('Câu một của cảnh 1. Câu hai của cảnh 1.')).toBeInTheDocument()
    expect(within(scenes[0]).queryByLabelText('Tiêu đề cảnh 1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Giao cho sinh viên' })).toBeDisabled()
    expect(screen.queryByLabelText('Video đã dựng')).not.toBeInTheDocument()

    await openScene(1)
    expect(within(scenes[0]).getByLabelText('Tiêu đề cảnh 1')).toHaveValue('Cảnh 1')
    expect(within(scenes[0]).getByLabelText('Ý 2 cảnh 1')).toHaveValue('Ý hai của cảnh 1')
    const sentence = within(scenes[0]).getByLabelText('Câu 1 cảnh 1')
    expect(sentence).toHaveValue('Câu một của cảnh 1.')
    // The slide comes first, and what the lecturer edits is below it.
    expect(slide.compareDocumentPosition(sentence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // One scene at a time.
    expect(screen.getByRole('button', { name: 'Sửa cảnh 2' })).toBeDisabled()
    // The order of the page: drawing, then rendering, then publishing.
    const draw = screen.getByRole('button', { name: 'Vẽ hình' })
    const renderButton = screen.getByRole('button', { name: 'Dựng video' })
    const publish = screen.getByRole('button', { name: 'Giao cho sinh viên' })
    expect(draw.compareDocumentPosition(renderButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(renderButton.compareDocumentPosition(publish) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    const box = screen.getByRole('textbox', { name: 'Yêu cầu chỉnh sửa' })
    await userEvent.type(box, 'Ngắn hơn')
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu' })).toBeDisabled()

    await userEvent.type(sentence, ' thêm')
    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    expect(api.saveVideoScenes).not.toHaveBeenCalled()
    expect(screen.getByText('Câu một của cảnh 1. Câu hai của cảnh 1.')).toBeInTheDocument()

    await userEvent.clear(box)
    await userEvent.type(box, 'Thêm một cảnh ví dụ')
    await userEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }))

    await waitFor(() => expect(api.reviseVideo).toHaveBeenCalledWith('video-1', 1, 'Thêm một cảnh ví dụ'))
    expect(await screen.findByText('Đã thêm một cảnh.')).toBeInTheDocument()
    expect(await sceneItems()).toHaveLength(3)
    expect(box).toHaveValue('')
  })

  it('shows the estimated length and blocks render over 8 minutes', async () => {
    // 10170 characters at 15 a second is 678 seconds, 11,3 minutes.
    const long = scene(1, { narration: 'a'.repeat(10170), narration_citations: [['c1']] })
    const problem = 'Video ước tính 11,3 phút, quá giới hạn 8 phút: hãy rút ngắn lời thoại'
    vi.mocked(api.getVideo).mockResolvedValue(video([long], { estimated_seconds: 678, render_problems: [problem] }))

    renderPage()

    expect(await screen.findByText(problem)).toBeInTheDocument()
    expect(screen.getByText(/^Quá 8 phút/)).toBeInTheDocument()
    const renderButton = screen.getByRole('button', { name: 'Dựng video' })
    expect(renderButton).toBeDisabled()
    // The reason is read before the button.
    expect(screen.getByText(problem).compareDocumentPosition(renderButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('marks flagged scenes and uncited sentences of the model', async () => {
    const flagged = scene(1, {
      narration_citations: [['c1'], []],
      flags: ['Ý 1: Ý này ngược nghĩa với nguồn', 'Lời thoại chỉ có 1 cảnh', 'Chưa có nguồn: Câu 2'],
    })
    vi.mocked(api.getVideo).mockResolvedValue(video([flagged, scene(2)]))
    vi.mocked(api.saveVideoScenes).mockImplementation(async (_id, version, edits) =>
      video(edits.map((e, i) => scene(i + 1, { ...e, flags: e.flags ?? [] })), { draft_version: version + 1 }))

    renderPage()
    const scenes = await sceneItems()

    expect(within(scenes[0]).getByText('Cần xem lại')).toBeInTheDocument()
    expect(within(scenes[1]).queryByText('Cần xem lại')).not.toBeInTheDocument()
    // Closed, the scene lists why, each reason with the bullet or sentence it is about.
    const reasons = within(scenes[0]).getByRole('list', { name: 'Lý do cảnh 1 cần xem lại' })
    expect(within(reasons).getAllByRole('listitem').map((li) => li.textContent)).toEqual(
      ['Ý 1: Ý này ngược nghĩa với nguồn', 'Lời thoại chỉ có 1 cảnh', 'Câu 2: chưa có nguồn'])

    await openScene(1)
    expect(within(scenes[0]).getByText('Lời thoại chỉ có 1 cảnh')).toBeInTheDocument()
    const bullet = within(within(scenes[0]).getByRole('list', { name: 'Ý chính cảnh 1' })).getAllByRole('listitem')[0]
    expect(within(bullet).getByText('Ý này ngược nghĩa với nguồn')).toBeInTheDocument()
    const sentences = within(within(scenes[0]).getByRole('list', { name: 'Lời thoại cảnh 1' })).getAllByRole('listitem')
    expect(within(sentences[0]).queryByText('Chưa có nguồn')).not.toBeInTheDocument()
    expect(within(sentences[1]).getByText('Chưa có nguồn')).toBeInTheDocument()
    // A missing source is never dismissed.
    expect(within(sentences[1]).queryByRole('button', { name: /Bỏ đánh dấu/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await userEvent.click(within(bullet).getByRole('button', { name: 'Bỏ đánh dấu ý 1 cảnh 1' }))
    expect(within(bullet).queryByText('Ý này ngược nghĩa với nguồn')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))

    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalledTimes(1))
    const [, , sent] = vi.mocked(api.saveVideoScenes).mock.calls[0]
    expect(sent[0].flags).toEqual(['Lời thoại chỉ có 1 cảnh'])
  })

  it('redraws one scene after its prompt is edited', async () => {
    const drawn = scene(2, { kind: 'illustration', image_prompt: 'Hàng đợi P1 P2 P3', has_image: true })
    const loaded = video([scene(1), drawn])
    vi.mocked(api.getVideo).mockResolvedValue(loaded)
    const edited = { ...drawn, image_prompt: 'Hàng đợi P1 P2 P3 P4', image_outdated: true }
    vi.mocked(api.saveVideoScenes).mockResolvedValue(video([scene(1), edited], { draft_version: 2 }))
    vi.mocked(api.drawIllustrations).mockResolvedValue({ job_id: 'job-2', scene_indexes: [1] })

    renderPage()
    await openScene(2)
    const prompt = screen.getByLabelText('Mô tả hình cảnh 2')
    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(2))
    await userEvent.type(prompt, ' P4')
    // The picture is drawn from the saved description, so an unsaved one cannot be drawn.
    expect(screen.getByRole('button', { name: 'Vẽ lại hình cảnh 2' })).toBeDisabled()
    expect(api.getScenePreview).toHaveBeenCalledTimes(2)

    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalledTimes(1))
    const [id, version, sent] = vi.mocked(api.saveVideoScenes).mock.calls[0]
    expect(id).toBe('video-1')
    expect(version).toBe(1)
    expect(sent.map((s) => s.from_index)).toEqual([0, 1])
    expect(sent[1]).toMatchObject({ kind: 'illustration', image_prompt: 'Hàng đợi P1 P2 P3 P4', formula: null })

    // Only the saved scene's slide is fetched again.
    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(3))
    expect(vi.mocked(api.getScenePreview).mock.calls[2][1]).toBe(1)

    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1), edited], {
      draft_version: 2, status: 'drawing', job: { id: 'job-2', job_type: 'video_images', status: 'running' },
    }))
    await userEvent.click(screen.getByRole('button', { name: 'Vẽ lại hình cảnh 2' }))
    expect(api.drawIllustrations).toHaveBeenCalledWith('video-1', [1])
    expect(await screen.findByText('Đang vẽ hình…')).toBeInTheDocument()
  })

  it('says once, as a toast, that a picture job failed while the page was closed', async () => {
    const failed = 'Không vẽ được hình cho cảnh 2: HTTP 402. Hãy vẽ lại các cảnh này.'
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1), scene(2)], { error: failed }))

    renderPage()

    const told = await screen.findByText(failed)
    expect(told.closest('[data-sonner-toaster]')).not.toBeNull()
    expect(screen.getAllByText(failed)).toHaveLength(1)
    expect(screen.getByRole('main')).not.toHaveTextContent('HTTP 402')
  })

  it('draws every picture that is missing or outdated with one button', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([
      scene(1, { kind: 'illustration', image_prompt: 'Một hàng đợi', needs_image: true }),
    ], { render_problems: ['Cảnh 1: chưa vẽ hình minh họa'] }))
    vi.mocked(api.drawIllustrations).mockRejectedValue(
      axiosFailure(429, 'Mỗi ngày bạn được vẽ tối đa 20 hình, hôm nay còn 0 hình. Hãy quay lại vào ngày mai.'))

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Vẽ hình' }))

    expect(api.drawIllustrations).toHaveBeenCalledWith('video-1', undefined)
    expect(await screen.findByText(/hôm nay còn 0 hình/)).toBeInTheDocument()
  })

  it('asks before rendering over the warnings, and renders when confirmed', async () => {
    const warning = 'Cảnh 1: hình được vẽ theo mô tả cũ; bấm Vẽ lại nếu cần'
    vi.mocked(api.getVideo).mockResolvedValue(video([
      scene(1, { kind: 'illustration', image_prompt: 'Mới', has_image: true, image_outdated: true }),
    ], { render_warnings: [warning] }))
    vi.mocked(api.renderVideo).mockResolvedValue({ job_id: 'job-3' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    expect(await screen.findByText(warning)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Dựng video' }))
    expect(confirm.mock.calls[0][0]).toContain(warning)
    expect(api.renderVideo).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Dựng video' }))
    expect(api.renderVideo).toHaveBeenCalledWith('video-1')
  })

  it('shows the rendered video while a new draft is being edited', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)], {
      status: 'ready', draft_version: 3, rendered_version: 2, duration_seconds: 291, published_at: '2026-11-12T01:00:00Z',
    }))
    vi.mocked(api.getVideoFile).mockResolvedValue({ url: 'https://s3.example/v.mp4?sig=1', via_api: false, filename: 'v.mp4' })

    renderPage()

    const player = await screen.findByLabelText('Video đã dựng')
    await waitFor(() => expect(player).toHaveAttribute('src', 'https://s3.example/v.mp4?sig=1'))
    expect(screen.getByText(/Bản nháp đã sửa sau lần dựng gần nhất/)).toBeInTheDocument()
    await openScene(1)
    await userEvent.type(screen.getByLabelText('Tiêu đề cảnh 1'), ' mới')
    expect(screen.getByLabelText('Video đã dựng')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ngừng giao' })).toBeEnabled()
  })

  it('plays a locally stored video through the API with the token', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)], { status: 'ready', rendered_version: 1 }))
    vi.mocked(api.getVideoFile).mockResolvedValue({ url: '/videos/video-1/file/content', via_api: true, filename: 'v.mp4' })
    vi.mocked(api.videoFileBlob).mockResolvedValue(new Blob(['mp4'], { type: 'video/mp4' }))

    const { unmount } = renderPage()

    const player = await screen.findByLabelText('Video đã dựng')
    await waitFor(() => expect(player.getAttribute('src')).toMatch(/^blob:/))
    expect(api.videoFileBlob).toHaveBeenCalledWith('/videos/video-1/file/content')
    const src = player.getAttribute('src')
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(src)
  })

  it('polls a running job and shows its error', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.getVideo)
      .mockResolvedValueOnce(video([scene(1)], { status: 'rendering', job: { id: 'job-3', job_type: 'video_render', status: 'running' } }))
      .mockResolvedValue(video([scene(1)], { status: 'failed', error: 'Dựng video thất bại: giọng đọc không trả về âm thanh.' }))

    renderPage()

    expect(await screen.findByText('Đang dựng video…')).toHaveAttribute('data-status', 'processing')
    expect(screen.getByRole('button', { name: 'Dựng video' })).toBeDisabled()
    await passTime(3600)

    // A job's result is a toast, like every other result, not a line on the page.
    const told = await screen.findByText('Dựng video thất bại: giọng đọc không trả về âm thanh.')
    expect(told.closest('[data-sonner-toaster]')).not.toBeNull()
    expect(screen.getByRole('main')).not.toHaveTextContent('giọng đọc không trả về âm thanh')
    expect(screen.queryByText('Đang dựng video…')).not.toBeInTheDocument()
    const calls = vi.mocked(api.getVideo).mock.calls.length
    await passTime(10_000, 1000)
    expect(vi.mocked(api.getVideo).mock.calls.length).toBe(calls)
  })

  it('says a save over a newer draft is refused, and reloads it', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)]))
    vi.mocked(api.saveVideoScenes).mockRejectedValue(
      axiosFailure(409, 'Bản nháp đã được lưu ở nơi khác. Hãy tải lại trang rồi sửa tiếp.'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await openScene(1)
    await userEvent.type(screen.getByLabelText('Tiêu đề cảnh 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))

    expect(await screen.findByText('Bản nháp đã được lưu ở nơi khác. Hãy tải lại trang rồi sửa tiếp.')).toBeInTheDocument()
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1, { title: 'Bản ở tab khác' })], { draft_version: 5 }))
    await userEvent.click(screen.getByRole('button', { name: 'Tải lại bản nháp' }))

    await openScene(1)
    expect(await screen.findByDisplayValue('Bản ở tab khác')).toBeInTheDocument()
  })

  it('saves a removed, a moved and an added scene at once, each with where it came from', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1), scene(2), scene(3)]))
    vi.mocked(api.saveVideoScenes).mockImplementation(async (_id, version, edits) =>
      video(edits.map((e, i) => scene(i + 1, { title: e.title })), { draft_version: version + 1 }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const sent = (call: number) => vi.mocked(api.saveVideoScenes).mock.calls[call][2].map((s) => [s.from_index, s.title])

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Xóa cảnh 1' }))
    expect(await screen.findByText('Đã xóa cảnh.')).toBeInTheDocument()
    expect(sent(0)).toEqual([[1, 'Cảnh 2'], [2, 'Cảnh 3']])

    await userEvent.click(screen.getByRole('button', { name: 'Đưa cảnh 2 lên' }))
    expect(await screen.findByText('Đã đổi thứ tự cảnh.')).toBeInTheDocument()
    expect(sent(1)).toEqual([[1, 'Cảnh 3'], [0, 'Cảnh 2']])

    await userEvent.click(screen.getByRole('button', { name: 'Thêm cảnh' }))
    await userEvent.type(screen.getByLabelText('Tiêu đề cảnh 3'), 'Cảnh mới')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalledTimes(3))
    expect(sent(2)).toEqual([[0, 'Cảnh 3'], [1, 'Cảnh 2'], [null, 'Cảnh mới']])
    expect(vi.mocked(api.saveVideoScenes).mock.calls[2][2][2].flags).toBeUndefined()
  })

  it('loads slides two at a time and asks again when the server is busy', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let inFlight = 0
    let most = 0
    let busyOnce = true
    vi.mocked(api.getScenePreview).mockImplementation(async (_id, index) => {
      inFlight++
      most = Math.max(most, inFlight)
      await new Promise((resolve) => setTimeout(resolve, 50))
      inFlight--
      if (index === 0 && busyOnce) {
        busyOnce = false
        throw axiosFailure(503, 'Máy chủ đang bận dựng slide khác, vui lòng thử lại sau giây lát.')
      }
      return new Blob(['png'])
    })
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1), scene(2), scene(3), scene(4)]))

    const { unmount } = renderPage()
    await sceneItems()
    await passTime(6000)

    for (const n of [1, 2, 3, 4]) expect(screen.getByRole('img', { name: `Slide cảnh ${n}` })).toBeInTheDocument()
    expect(most).toBeLessThanOrEqual(2)
    expect(vi.mocked(api.getScenePreview).mock.calls.filter(([, index]) => index === 0)).toHaveLength(2)
    unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(4)
  })

  it('asks before sidebar and browser Back leave unsaved edits, stays when refused, and proceeds when confirmed', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)]))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(false).mockReturnValueOnce(true)

    const { router } = renderPage()
    await openScene(1)
    await userEvent.type(screen.getByLabelText('Câu 1 cảnh 1'), '!')
    const sidebar = within(screen.getByRole('navigation', { name: 'Điều hướng chính' }))
    await userEvent.click(sidebar.getByRole('link', { name: 'Môn học' }))
    expect(confirm).toHaveBeenCalledWith('Các thay đổi chưa lưu sẽ mất. Rời trang?')
    expect(screen.queryByText('Danh sách môn học')).not.toBeInTheDocument()
    expect((screen.getByLabelText('Câu 1 cảnh 1') as HTMLTextAreaElement).value).toContain('!')

    await act(() => router.navigate(-1))
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(screen.getByLabelText('Câu 1 cảnh 1')).toBeInTheDocument()

    await act(() => router.navigate(-1))
    expect(await screen.findByText('Danh sách môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(3)
  })

  it('asks before logging out with unsaved edits, once, and logs out when told to', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)]))
    vi.mocked(api.getMoodleAccount).mockResolvedValue({ email: 'lan@x.edu', status: 'not_linked' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)

    renderPage()
    await openScene(1)
    await userEvent.type(screen.getByLabelText('Câu 1 cảnh 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: /^Tài khoản Lan/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Câu 1 cảnh 1')).toBeInTheDocument()
    expect(api.logout).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }))
    expect(await screen.findByText('Trang đăng nhập')).toBeInTheDocument()
    expect(api.logout).toHaveBeenCalledWith('r')
    // The redirect to the login page that follows is not asked about a second time.
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  it('leaves for the course page without asking once it is deleted, unsaved edits and all', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([scene(1)]))
    vi.mocked(api.deleteVideo).mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()
    await openScene(1)
    await userEvent.type(screen.getByLabelText('Câu 1 cảnh 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Xóa video' }))

    expect(await screen.findByText('Trang môn học')).toBeInTheDocument()
    expect(confirm).toHaveBeenCalledTimes(1) // the delete's own question only
  })

  it('shows a missing video as not found, and a failed load with a retry', async () => {
    vi.mocked(api.getVideo).mockRejectedValueOnce(axiosFailure(404, 'Video not found'))
    const { unmount } = renderPage()
    expect(await screen.findByText('Không tìm thấy video.')).toBeInTheDocument()
    unmount()

    vi.mocked(api.getVideo).mockRejectedValueOnce(axiosFailure(500, 'boom')).mockResolvedValue(video([scene(1)]))
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Thử lại' }))
    expect(await screen.findByRole('button', { name: 'Sửa cảnh 1' })).toBeInTheDocument()
  })

  it('follows a script being generated', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.getVideo)
      .mockResolvedValueOnce(video([], { status: 'generating', job: { id: 'job-1', job_type: 'video_script', status: 'running' } }))
      .mockResolvedValue(video([scene(1)]))

    renderPage()
    expect(await screen.findByText('Đang tạo…')).toBeInTheDocument()
    await passTime(3600)
    expect(await screen.findByRole('button', { name: 'Sửa cảnh 1' })).toBeInTheDocument()
  })

  it('keeps the saved draft when a poll that started before the save answers after it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const running = { id: 'job-2', job_type: 'video_images' as const, status: 'running' as const }
    vi.mocked(api.getVideo).mockResolvedValueOnce(video([scene(1)], { status: 'drawing', job: running }))
    let answerPoll: (v: VideoDetail) => void = () => {}
    vi.mocked(api.getVideo).mockImplementationOnce(() => new Promise((resolve) => { answerPoll = resolve }))
    vi.mocked(api.saveVideoScenes).mockResolvedValue(
      video([scene(1, { title: 'Đã lưu', author: 'lecturer' })], { draft_version: 2, status: 'drawing', job: running }))

    renderPage()
    await openScene(1)
    const title = screen.getByLabelText('Tiêu đề cảnh 1')
    await passTime(3300)
    // The poll is out; the lecturer saves before it answers.
    expect(api.getVideo).toHaveBeenCalledTimes(2)
    await userEvent.clear(title)
    await userEvent.type(title, 'Đã lưu')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await screen.findByText('Đã lưu.')

    await act(async () => answerPoll(video([scene(1)], { draft_version: 1, status: 'draft', job: null })))

    await openScene(1)
    expect(screen.getByLabelText('Tiêu đề cảnh 1')).toHaveValue('Đã lưu')
    // Nothing of the late answer is taken, not even its job or status: it is older than the save.
    expect(screen.getByText('Đang vẽ hình…')).toBeInTheDocument()
    expect(screen.queryByText('Đã vẽ xong hình.')).not.toBeInTheDocument()
    vi.mocked(api.saveVideoScenes).mockClear()
    await userEvent.type(screen.getByLabelText('Tiêu đề cảnh 1'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalled())
    expect(vi.mocked(api.saveVideoScenes).mock.calls[0][1]).toBe(2)
  })

  it('never asks for the detail again while a poll is still out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const running = { id: 'job-3', job_type: 'video_render' as const, status: 'running' as const }
    vi.mocked(api.getVideo).mockResolvedValueOnce(video([scene(1)], { status: 'rendering', job: running }))
    vi.mocked(api.getVideo).mockImplementation(() => new Promise(() => {}))

    const { unmount } = renderPage()
    await screen.findByRole('button', { name: 'Sửa cảnh 1' })
    await passTime(15_000, 1000)

    expect(api.getVideo).toHaveBeenCalledTimes(2)
    unmount()
  })

  it('turns an illustration into a text slide when its description is emptied', async () => {
    const drawn = scene(1, { kind: 'illustration', image_prompt: 'Hàng đợi P1 P2', has_image: true })
    vi.mocked(api.getVideo).mockResolvedValue(video([drawn]))
    vi.mocked(api.saveVideoScenes).mockResolvedValue(video([{ ...drawn, image_prompt: null, has_image: false }], { draft_version: 2 }))

    renderPage()
    await openScene(1)
    const prompt = screen.getByLabelText('Mô tả hình cảnh 1')
    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(1))
    await userEvent.clear(prompt)
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))

    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalled())
    expect(vi.mocked(api.saveVideoScenes).mock.calls[0][2][0].image_prompt).toBeNull()
    // The slide is asked for again, now printed without the picture, and there is nothing to draw.
    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: /hình cảnh 1/ })).not.toBeInTheDocument()
  })

  it('handles lecturer uncited sentences, table bar formatting, and accessible descriptions for field/scene flags', async () => {
    vi.mocked(api.getVideo).mockResolvedValue(video([
      scene(1, { kind: 'table', table: [['A', 'B']], author: 'lecturer', narration_citations: [['c1'], []] }),
      scene(2, {
        kind: 'formula',
        formula: 'x^2',
        narration_citations: [['c1'], []],
        flags: ['Câu 2: Sai số liệu', 'Công thức: Sai dấu', 'Lời thoại chỉ có 1 cảnh'],
      }),
    ]))

    renderPage()
    const [first] = await sceneItems()

    expect(within(first).queryByText('Chưa có nguồn')).not.toBeInTheDocument()
    expect(within(first).queryByText('Cần xem lại')).not.toBeInTheDocument()
    await openScene(1)
    expect(screen.getByLabelText('Bảng cảnh 1')).toHaveValue('A | B')
    await userEvent.click(within(first).getByRole('button', { name: /Thêm câu/ }))
    await userEvent.type(within(first).getAllByRole('textbox').at(-1)!, 'Câu giảng viên thêm.')
    expect(within(first).queryByText('Chưa có nguồn')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Hủy' }))
    await openScene(2)

    expect(screen.getByLabelText('Câu 2 cảnh 2')).toHaveAccessibleDescription(/Chưa có nguồn.*Sai số liệu/)
    expect(screen.getByLabelText('Câu 1 cảnh 2')).not.toHaveAccessibleDescription()
    expect(screen.getByLabelText('Công thức cảnh 2')).toHaveAccessibleDescription('Sai dấu')
    expect(screen.getByLabelText('Tiêu đề cảnh 2')).toHaveAccessibleDescription('Lời thoại chỉ có 1 cảnh')
  })

  it('says the job is queued and follows it when the detail cannot be read after asking', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.mocked(api.getVideo)
      .mockResolvedValueOnce(video([scene(1)]))
      .mockRejectedValueOnce(axiosFailure(500, 'boom'))
      .mockResolvedValue(video([scene(1)], { status: 'ready', rendered_version: 1 }))
    vi.mocked(api.renderVideo).mockResolvedValue({ job_id: 'job-3' })
    vi.mocked(api.getVideoFile).mockResolvedValue({ url: 'https://s3.example/v.mp4', via_api: false, filename: 'v.mp4' })

    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Dựng video' }))

    expect(await screen.findByText(/Đã gửi yêu cầu dựng video/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await passTime(3600)
    expect(await screen.findByText('Đã dựng xong video.')).toBeInTheDocument()
    expect(screen.getByLabelText('Video đã dựng')).toBeInTheDocument()
  })

  it('shows a redrawn slide even when the drawing was over before the first poll', async () => {
    const drawn = scene(1, { kind: 'illustration', image_prompt: 'Hàng đợi', has_image: true })
    vi.mocked(api.getVideo).mockResolvedValue(video([drawn]))
    vi.mocked(api.drawIllustrations).mockResolvedValue({ job_id: 'job-2', scene_indexes: [0] })

    renderPage()
    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('button', { name: 'Vẽ lại hình cảnh 1' }))

    await waitFor(() => expect(api.getScenePreview).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('Đã vẽ xong hình.')).toBeInTheDocument()
  })

  it('starts the player afresh for a new render after a failed one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const rendering = { id: 'job-3', job_type: 'video_render' as const, status: 'running' as const }
    vi.mocked(api.getVideo)
      .mockResolvedValueOnce(video([scene(1)], { status: 'rendering', rendered_version: 1, job: rendering }))
      .mockResolvedValue(video([scene(1)], { status: 'ready', rendered_version: 2 }))
    vi.mocked(api.getVideoFile)
      .mockRejectedValueOnce(axiosFailure(404, 'Video chưa được dựng.'))
      .mockResolvedValue({ url: 'https://s3.example/v2.mp4', via_api: false, filename: 'v.mp4' })

    renderPage()
    expect(await screen.findByText(/Không tải được video đã dựng/)).toBeInTheDocument()
    await passTime(3600)

    await waitFor(() => expect(screen.getByLabelText('Video đã dựng')).toHaveAttribute('src', 'https://s3.example/v2.mp4'))
  })

  it('shows an inline save-banner above video actions when a scene is open or dirty, and saves, closes, or cancels from it', async () => {
    const initial = video([scene(1), scene(2, { kind: 'illustration', image_prompt: 'Hình', has_image: true })])
    vi.mocked(api.getVideo).mockResolvedValue(initial)
    vi.mocked(api.saveVideoScenes).mockResolvedValue(
      video([scene(1), scene(2, { kind: 'illustration', title: 'Cảnh 2 đã lưu', image_prompt: 'Hình', has_image: true })], { draft_version: 2 }),
    )

    renderPage()

    await openScene(2)
    expect(screen.getByText(/Đang mở sửa Cảnh 2\. Đóng khung sửa trước khi/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Vẽ hình' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Dựng video' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Đóng sửa cảnh 2' }))
    expect(screen.queryByLabelText('Tiêu đề cảnh 2')).not.toBeInTheDocument()
    expect(screen.queryByText(/Đang mở sửa Cảnh 2/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dựng video' })).toBeEnabled()

    await openScene(2)
    const titleInput = screen.getByLabelText('Tiêu đề cảnh 2')
    await userEvent.clear(titleInput)
    expect(screen.getByText(/Cảnh 2 có thay đổi chưa lưu/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lưu cảnh 2' })).toBeDisabled()

    await userEvent.type(titleInput, 'Cảnh 2 đã lưu')
    const saveBannerBtn = screen.getByRole('button', { name: 'Lưu cảnh 2' })
    expect(saveBannerBtn).toBeEnabled()
    await userEvent.click(saveBannerBtn)

    await waitFor(() => expect(api.saveVideoScenes).toHaveBeenCalledTimes(1))
    expect(screen.queryByText(/có thay đổi chưa lưu/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dựng video' })).toBeEnabled()

    await openScene(2)
    await userEvent.type(screen.getByLabelText('Tiêu đề cảnh 2'), ' nháp')
    expect(screen.getByText(/Cảnh 2 có thay đổi chưa lưu/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Hủy sửa cảnh 2' }))
    expect(screen.queryByLabelText('Tiêu đề cảnh 2')).not.toBeInTheDocument()
    expect(screen.queryByText(/có thay đổi chưa lưu/)).not.toBeInTheDocument()
  })
})
