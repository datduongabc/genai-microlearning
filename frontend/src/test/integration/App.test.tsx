import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { VideoDetail } from '../../api'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return Object.fromEntries(
    Object.entries(actual).map(([name, value]) => [
      name,
      typeof value === 'function' && !['apiErrorMessage', 'api'].includes(name) ? vi.fn() : value,
    ]),
  )
})

const video = {
  id: 'video-1', course_id: 'course-1', document_id: 'doc-1', focus: 'FCFS', output_language: 'vi',
  model_alias: 'gemini-flash', voice_alias: 'voice-heygen', image_alias: 'image-nano-banana', status: 'draft',
  scene_count: 1, draft_version: 1, rendered_version: null, duration_seconds: null, error: null,
  published_at: null, created_at: '2026-11-12T00:00:00Z', estimated_seconds: 60, max_seconds: 480,
  render_problems: [], render_warnings: [], job: null, cost: null,
  scenes: [{
    kind: 'summary', title: 'Cảnh 1', bullets: ['Ý một'], bullet_citations: [[]], formula: null, code: null,
    table: null, image_prompt: null, narration: 'Câu một.', narration_citations: [[]], citations: [],
    author: 'model', flags: [], has_image: false, image_outdated: false, needs_image: false, sources: [],
  }],
} as VideoDetail

beforeEach(() => {
  localStorage.setItem('auth', JSON.stringify({
    token: 't', refreshToken: 'r', user: { id: 'u1', email: 'lan@x.edu', name: 'Lan', can_author: true, is_admin: false },
  }))
  URL.createObjectURL = vi.fn(() => 'blob:preview')
  URL.revokeObjectURL = vi.fn()
  vi.mocked(api.getVideo).mockResolvedValue(video)
  vi.mocked(api.getScenePreview).mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
  vi.mocked(api.getMediaModels).mockResolvedValue({ voices: [], images: [], default_voice: '', default_image: '' })
})

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('App', () => {
  // The editors' leave guard (useBlocker) throws outside a data router, so this fails if App goes
  // back to <BrowserRouter>. The router is built when the module loads, hence the late import.
  it('serves the video editor on the data router the leave guard needs', async () => {
    window.history.pushState(null, '', '/videos/video-1')
    const { default: App } = await import('../../App')

    render(<App />)

    expect(await screen.findByLabelText('Câu 1 cảnh 1')).toBeInTheDocument()
  })

  // A data router catches render errors in its own default screen; this fails if the route errors
  // stop reaching ErrorBoundary and its reload wording.
  it('shows the reload screen when a page fails to render', async () => {
    vi.doMock('../../pages/CoursesPage', () => ({
      CoursesPage: () => {
        throw new TypeError('Failed to fetch dynamically imported module: /assets/x.js')
      },
    }))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    window.history.pushState(null, '', '/courses')
    const { default: App } = await import('../../App')

    render(<App />)

    expect(await screen.findByText('Đã có phiên bản mới, tải lại trang.')).toBeInTheDocument()
    vi.doUnmock('../../pages/CoursesPage')
  })
})
