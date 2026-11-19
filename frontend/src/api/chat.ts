import { fetchWithAuth } from './client'

export interface ChatCitation {
  chunk_id: string
  source_ref: string
}

export interface ChatReply {
  id: string
  role: string
  content: string
  citations: ChatCitation[]
}

type ChatEvent =
  | { name: 'session'; data: { session_id: string } }
  | { name: 'delta'; data: { text: string } }
  | { name: 'done'; data: ChatReply }
  | { name: 'error'; data: { detail: string } }

const STREAM_BROKE = 'Mất kết nối trước khi có đủ câu trả lời, vui lòng thử lại.'

// Splits the text received so far into finished events, and returns what is left for the
// next piece. A line starting with ":" is the server's heartbeat.
export function parseChatEvents(buffer: string): { events: ChatEvent[]; rest: string } {
  const blocks = buffer.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: ChatEvent[] = []
  for (const block of blocks) {
    const lines = block.split('\n')
    const name = lines.find((line) => line.startsWith('event: '))?.slice(7)
    const data = lines.find((line) => line.startsWith('data: '))?.slice(6)
    if (name && data) events.push({ name, data: JSON.parse(data) } as ChatEvent)
  }
  return { events, rest }
}

async function refusalMessage(response: Response): Promise<string> {
  if (response.status === 429) return 'Bạn thao tác quá nhanh, vui lòng đợi một chút rồi thử lại.'
  if (response.status === 503) return 'Hệ thống đang bận, vui lòng thử lại sau ít phút.'
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  return typeof body?.detail === 'string' && body.detail ? body.detail : 'Không gửi được câu hỏi, vui lòng thử lại.'
}

// Resolves with the saved reply. Rejects with a message to show when the question was refused,
// the answer failed, or the connection ended before the answer did.
export async function streamCourseChat(
  courseId: string,
  message: string,
  sessionId: string | undefined,
  on: { session: (id: string) => void; delta: (text: string) => void },
  signal?: AbortSignal,
): Promise<ChatReply> {
  const response = await fetchWithAuth(`/courses/${courseId}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  })
  if (!response.ok) throw new Error(await refusalMessage(response))
  if (!response.body) throw new Error(STREAM_BROKE)

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) throw new Error(STREAM_BROKE)
    const { events, rest } = parseChatEvents(buffer + value)
    buffer = rest
    for (const event of events) {
      if (event.name === 'session') on.session(event.data.session_id)
      else if (event.name === 'delta') on.delta(event.data.text)
      else if (event.name === 'done') return event.data
      else throw new Error(event.data.detail)
    }
  }
}

interface LatestCourseChat {
  session_id: string | null
  messages: ChatReply[]
}

// An empty conversation on the server, so a reload brings it back instead of the one it replaced.
export async function startNewCourseChat(courseId: string): Promise<string> {
  const response = await fetchWithAuth(`/courses/${courseId}/chat/sessions`, { method: 'POST' })
  if (!response.ok) throw new Error('Không mở được cuộc trò chuyện mới.')
  return (await response.json()).session_id
}

// The course chat used most recently, so a reload carries on with it.
export async function getLatestCourseChat(courseId: string, signal?: AbortSignal): Promise<LatestCourseChat> {
  const response = await fetchWithAuth(`/courses/${courseId}/chat/latest`, { signal })
  if (!response.ok) throw new Error('Không tải được cuộc trò chuyện trước.')
  return response.json()
}
