/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as AuthHookModule from '../../../auth/useAuth'
import * as api from '../../../api'
import { LearnVideoPage } from '../../../pages/LearnVideoPage'

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

const video = {
  id: 'v1', course_id: 'course-1', title: 'Giới thiệu heap', duration_seconds: 125,
  rendered_version: 1, via_api: false, file_url: 'https://s3.example/v1.mp4?sig=1',
  source_document: 'bai-giang.pdf',
  scenes: [
    { title: 'Mở đầu', start_seconds: 0, sources: ['trang 2'] },
    { title: 'Heap là gì', start_seconds: 30.5, sources: ['trang 3 · Heap', 'trang 4'] },
    { title: 'Tổng kết', start_seconds: 95, sources: [] },
  ],
}

function renderPage() {
  vi.spyOn(AuthHookModule, 'useAuth').mockReturnValue({
    token: 't', refreshToken: 'r',
    user: { id: 'u', email: 'e', name: 'Sinh Viên A', is_admin: false, can_author: false },
    login: vi.fn(), loginWithLaunchCode: vi.fn(), logout: vi.fn(),
  })
  return render(
    <MemoryRouter initialEntries={['/learn/course-1/videos/v1']}>
      <Routes>
        <Route path="/learn/:courseId" element={<p>Trang học</p>} />
        <Route path="/learn/:courseId/videos/:videoId" element={<LearnVideoPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function player() {
  return document.querySelector('video') as HTMLVideoElement
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getLearnVideo).mockResolvedValue(video)
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
})

describe('LearnVideoPage', () => {
  it('plays the video and lists the sources of its scenes', async () => {
    renderPage()

    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    expect(player()).toHaveAttribute('src', video.file_url)
    expect(player()).toHaveAttribute('controls')
    expect(player()).toHaveAttribute('preload', 'metadata')
    expect(player()).toHaveAttribute('playsinline')
    const second = screen.getByRole('button', { name: /Heap là gì/ })
    // Floored, as the player shows it.
    expect(second).toHaveTextContent('0:30')
    // The file is named once for the list; each scene says only where in it, on one line.
    expect(screen.getByText('Nguồn: bai-giang.pdf')).toBeInTheDocument()
    expect(screen.getAllByText(/bai-giang\.pdf/)).toHaveLength(1)
    expect(second.querySelectorAll('.learn-scene-source')).toHaveLength(1)
    expect(second).toHaveTextContent('Nguồn: trang 3 · Heap; trang 4')
    expect(screen.getByRole('button', { name: /Tổng kết/ })).toHaveTextContent('1:35')
  })

  it('seeks to a scene and plays when it is clicked, with the keyboard too', async () => {
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: /Heap là gì/ }))
    expect(player().currentTime).toBe(30.5)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1)

    screen.getByRole('button', { name: /Tổng kết/ }).focus()
    await userEvent.keyboard('{Enter}')
    expect(player().currentTime).toBe(95)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2)
  })

  it('marks the scene that is playing', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    expect(screen.getByRole('button', { name: /Mở đầu/ })).toHaveAttribute('aria-current', 'true')

    player().currentTime = 40
    fireEvent.timeUpdate(player())

    expect(screen.getByRole('button', { name: /Heap là gì/ })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /Mở đầu/ })).not.toHaveAttribute('aria-current')
  })

  it('asks for a fresh link once when the link has expired, and resumes where it was', async () => {
    vi.mocked(api.getLearnVideo)
      .mockResolvedValueOnce(video)
      .mockResolvedValueOnce({ ...video, file_url: 'https://s3.example/v1.mp4?sig=2' })
    renderPage()
    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    player().currentTime = 42
    Object.defineProperty(player(), 'paused', { value: false, configurable: true })

    fireEvent.error(player())

    await waitFor(() => expect(player()).toHaveAttribute('src', 'https://s3.example/v1.mp4?sig=2'))
    fireEvent.loadedMetadata(player())
    expect(player().currentTime).toBe(42)
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
  })

  it('starts at the matching scene of a newer render when the new link comes with one', async () => {
    const newer = {
      ...video, rendered_version: 2, file_url: 'https://s3.example/v2.mp4?sig=1',
      scenes: [
        { title: 'Mở đầu mới', start_seconds: 0, sources: [] },
        { title: 'Heap mới', start_seconds: 50, sources: [] },
      ],
    }
    vi.mocked(api.getLearnVideo).mockResolvedValueOnce(video).mockResolvedValueOnce(newer)
    renderPage()
    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    player().currentTime = 40
    fireEvent.timeUpdate(player())
    fireEvent.error(player())

    await waitFor(() => expect(player()).toHaveAttribute('src', newer.file_url))
    fireEvent.loadedMetadata(player())

    // Scene 2 (index 1) was playing: it is at 50 s in the new file, and 40 s would be elsewhere.
    expect(player().currentTime).toBe(50)
    expect(screen.getByRole('status')).toHaveTextContent('Giảng viên vừa cập nhật video.')
    expect(screen.getByRole('button', { name: /Heap mới/ })).toHaveAttribute('aria-current', 'true')
    expect(screen.queryByRole('button', { name: /Tổng kết/ })).not.toBeInTheDocument()
  })

  it('starts at the beginning when the scene that was playing is gone from the newer render', async () => {
    const newer = {
      ...video, rendered_version: 2, file_url: 'https://s3.example/v2.mp4?sig=1',
      scenes: [{ title: 'Chỉ một cảnh', start_seconds: 0, sources: [] }],
    }
    vi.mocked(api.getLearnVideo).mockResolvedValueOnce(video).mockResolvedValueOnce(newer)
    renderPage()
    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    player().currentTime = 100
    fireEvent.timeUpdate(player())
    fireEvent.error(player())

    await waitFor(() => expect(player()).toHaveAttribute('src', newer.file_url))
    fireEvent.loadedMetadata(player())

    expect(player().currentTime).toBe(0)
  })

  it('renews again at a second expiry once the file has played, but not for a file that never loads', async () => {
    const link = (n: number) => ({ ...video, file_url: `https://s3.example/v1.mp4?sig=${n}` })
    vi.mocked(api.getLearnVideo).mockResolvedValueOnce(link(2)).mockResolvedValueOnce(link(3))
    renderPage()
    await screen.findByRole('heading', { name: 'Giới thiệu heap' })

    fireEvent.error(player())
    await waitFor(() => expect(player()).toHaveAttribute('src', link(2).file_url))
    fireEvent.loadedMetadata(player())
    fireEvent.error(player())
    await waitFor(() => expect(player()).toHaveAttribute('src', link(3).file_url))

    // The third link never loads: no fourth.
    fireEvent.error(player())
    expect(await screen.findByRole('alert')).toHaveTextContent('Không phát được video')
    // The page's own load, and the two renewals.
    expect(api.getLearnVideo).toHaveBeenCalledTimes(3)
  })

  it('fetches the file with the token when there is no S3 link', async () => {
    vi.mocked(api.getLearnVideo).mockResolvedValue({ ...video, via_api: true, file_url: '/learn/videos/v1/file/content' })
    vi.mocked(api.videoFileBlob).mockResolvedValue(new Blob(['x']))
    URL.createObjectURL = vi.fn(() => 'blob:v1')
    URL.revokeObjectURL = vi.fn()

    renderPage()

    await waitFor(() => expect(player()).toHaveAttribute('src', 'blob:v1'))
    expect(api.videoFileBlob).toHaveBeenCalledWith('/learn/videos/v1/file/content')
  })

  it('shows an error and a way back when the video cannot be loaded', async () => {
    vi.mocked(api.getLearnVideo).mockRejectedValue(new Error('x'))

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tải được video')
    expect(screen.getByRole('link', { name: 'Về trang học' })).toHaveAttribute('href', '/learn/course-1')
  })

  it('works at 390 px wide', async () => {
    renderPage()

    await screen.findByRole('heading', { name: 'Giới thiệu heap' })
    // jsdom has no layout, so what can be checked is that nothing is given a width that a phone cannot fit.
    // That the video is full width and the page does not scroll sideways at 390 px is checked in a real browser.
        expect(player().getAttribute('style')).toBeNull()
    expect(player()).toHaveClass('video-player')
    const video_rule = /\.video-player\s*\{([^}]*)\}/.exec(indexCss)?.[1] ?? ''
    const learn_rule = /\.learn-video\s*\{([^}]*)\}/.exec(indexCss)?.[1] ?? ''
    expect(video_rule).toMatch(/width:\s*100%/)
    expect(learn_rule).toMatch(/max-width:\s*100%/)
    // The colour is a token, not a literal.
    expect(video_rule + learn_rule).not.toMatch(/#[0-9a-f]{3,6}/i)
    const scene = screen.getByRole('button', { name: /Mở đầu/ })
    expect(scene).toHaveClass('learn-scene')
    const scene_rule = /\.learn-scene\s*\{([^}]*)\}/.exec(indexCss)?.[1] ?? ''
    expect(scene_rule).toMatch(/overflow-wrap:\s*anywhere/)
    for (const rule of [video_rule, learn_rule, scene_rule]) expect(rule).not.toMatch(/(?<![-\w])(min-)?width:\s*\d+px/)
  })
})
