import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseChatEvents } from '../../../api'
import { CourseChat } from '../../../components/CourseChat'

function event(name: string, data: unknown) {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
}

function stream(...pieces: string[]) {
  return new Response(new ReadableStream({
    start(controller) {
      pieces.forEach((piece) => controller.enqueue(new TextEncoder().encode(piece)))
      controller.close()
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const saved = (content: string) => ({
  id: 'm1', role: 'assistant', content, citations: [{ chunk_id: 'c1', source_ref: 'Chương 3, trang 4' }],
})

async function ask(question: string) {
  await userEvent.type(screen.getByLabelText('Câu hỏi của bạn'), question)
  await userEvent.click(screen.getByRole('button', { name: 'Gửi câu hỏi' }))
}

describe('CourseChat', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('auth', JSON.stringify({ token: 'old', refreshToken: 'r' }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('shows the answer with its sources and keeps the session for the next question', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('session', { session_id: 's1' }), ': ping\n\n', event('delta', { text: 'Tiến ' }),
      event('delta', { text: 'trình' }), event('done', saved('Tiến trình là chương trình đang chạy.'))))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Có.'))))
    render(<CourseChat courseId="course-1" />)

    await ask('Tiến trình là gì?')
    expect(await screen.findByText('Tiến trình là chương trình đang chạy.')).toBeInTheDocument()
    expect(screen.getByText('Nguồn: Chương 3, trang 4')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/courses/course-1/chat/stream')
    expect(init.headers.Authorization).toBe('Bearer old')

    await ask('Còn gì nữa?')
    await screen.findByText('Có.')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).session_id).toBe('s1')
  })

  it('shows the error the server sends once the answer has begun', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('delta', { text: 'Một phần' }), event('error', { detail: 'Không tạo được câu trả lời.' })))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tạo được câu trả lời.')
    expect(screen.getByText('Một phần')).toBeInTheDocument()
  })

  it('says so when the connection ends before the answer does', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('delta', { text: 'Dở' })))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    expect(await screen.findByRole('alert')).toHaveTextContent('Mất kết nối')
  })

  it('explains a busy server', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 }))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    expect(await screen.findByRole('alert')).toHaveTextContent('Hệ thống đang bận')
  })

  it('renews an expired session and sends the question again', async () => {
    const axios = (await import('axios')).default
    const post = vi.spyOn(axios, 'post').mockResolvedValue({ data: { access_token: 'new', refresh_token: 'r2' } })
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Đã trả lời.'))))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    await screen.findByText('Đã trả lời.')
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe('Bearer new')
    post.mockRestore()
  })

  it('stops the stream when the page is left', async () => {
    fetchMock.mockReturnValueOnce(new Promise(() => {}))
    const { unmount } = render(<CourseChat courseId="course-1" />)
    await ask('Hỏi')
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    unmount()

    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  })
})

describe('parseChatEvents', () => {
  it('keeps an event cut in two pieces for the next read', () => {
    const whole = event('delta', { text: 'a' })
    const first = parseChatEvents(whole.slice(0, 10))
    expect(first.events).toEqual([])
    expect(parseChatEvents(first.rest + whole.slice(10)).events).toEqual([{ name: 'delta', data: { text: 'a' } }])
  })
})
