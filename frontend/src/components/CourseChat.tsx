import { useEffect, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import Markdown from 'react-markdown'
import { streamCourseChat, type ChatCitation } from '../api'
import { courseChatSchema, type CourseChatFormValues } from '../lib/formSchemas'

interface Turn {
  question: string
  answer: string
  citations: ChatCitation[]
  error?: string
}

interface ChunkPlace {
  heading?: string
  page?: number
  slide_index?: number
  spans?: ChunkPlace[]
}

// A chunk's location is stored as JSON: {"page": 27, "heading": "..."}, or a list of those in
// "spans" when it crosses pages. Its first place is enough to find it.
export function sourceLabel(ref: string): string {
  try {
    const stored = JSON.parse(ref) as ChunkPlace
    const place = stored.spans?.[0] ?? stored
    const where = place.page ? `trang ${place.page}` : place.slide_index ? `slide ${place.slide_index}` : ''
    return [place.heading, where].filter(Boolean).join(', ') || 'Tài liệu môn học'
  } catch {
    return ref || 'Tài liệu môn học'
  }
}

// Questions about the course's documents, with the answer appearing as it is written.
export function CourseChat({ courseId }: { courseId: string }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const sessionId = useRef<string | undefined>(undefined)
  const leaving = useRef<AbortController | null>(null)
  useEffect(() => () => leaving.current?.abort(), [])
  const {
    register,
    handleSubmit,
    setValue,
    control,
    formState: { errors, isSubmitting: sending },
  } = useForm<CourseChatFormValues>({ resolver: zodResolver(courseChatSchema), defaultValues: { message: '' } })
  const blank = !useWatch({ control, name: 'message' }).trim()

  const updateLast = (change: (turn: Turn) => Turn) =>
    setTurns((previous) => [...previous.slice(0, -1), change(previous[previous.length - 1])])

  async function send({ message }: CourseChatFormValues) {
    setTurns((previous) => [...previous, { question: message, answer: '', citations: [] }])
    // Not reset(): it would also end isSubmitting while the answer is still arriving.
    setValue('message', '')
    const controller = new AbortController()
    leaving.current = controller
    try {
      const reply = await streamCourseChat(courseId, message, sessionId.current, {
        session: (id) => { sessionId.current = id },
        delta: (text) => updateLast((turn) => ({ ...turn, answer: turn.answer + text })),
      }, controller.signal)
      updateLast((turn) => ({ ...turn, answer: reply.content, citations: reply.citations }))
    } catch (err) {
      if (controller.signal.aborted) return
      const detail = err instanceof Error && !(err instanceof TypeError)
        ? err.message
        : 'Không kết nối được máy chủ, vui lòng thử lại.'
      updateLast((turn) => ({ ...turn, error: detail }))
    }
  }

  return <section className="mt-6 rounded-[3px] border border-gray-200 bg-white p-6">
    <h3 className="text-sm font-semibold">Hỏi đáp về tài liệu</h3>
    <div role="log" aria-label="Hội thoại" className="mt-3 flex flex-col gap-3">
      {turns.map((turn, index) => {
        const writing = sending && index === turns.length - 1
        return <div key={index} className="text-sm" aria-busy={writing}>
          <p className="font-medium">{turn.question}</p>
          {turn.answer && <div className="mt-1 [&_li]:ml-5 [&_ol]:list-decimal [&_p]:mb-2 [&_strong]:font-semibold [&_ul]:list-disc">
            <Markdown>{turn.answer}</Markdown>
          </div>}
          {writing && !turn.answer && <p className="mt-1 text-gray-500">Đang tìm trong tài liệu...</p>}
          {turn.citations.length > 0 && <p className="mt-1 text-xs text-gray-500">
            Nguồn: {[...new Set(turn.citations.map((c) => sourceLabel(c.source_ref)))].join('; ')}
          </p>}
          {turn.error && <p role="alert" className="mt-1 text-red-700">{turn.error}</p>}
        </div>
      })}
    </div>
    <form onSubmit={handleSubmit(send)} noValidate className="mt-3 flex flex-col gap-2">
      <label htmlFor="course-chat-message" className="text-sm">Câu hỏi của bạn</label>
      <textarea id="course-chat-message" maxLength={4000} rows={3} disabled={sending}
        {...register('message')} className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta" />
      {errors.message && <p className="text-xs text-red-600">{errors.message.message}</p>}
      <button type="submit" disabled={sending || blank}
        className="w-fit rounded-[3px] bg-terracotta px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
        {sending ? 'Đang trả lời...' : 'Gửi câu hỏi'}
      </button>
    </form>
  </section>
}
