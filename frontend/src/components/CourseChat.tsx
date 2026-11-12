import { useEffect, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import Markdown from 'react-markdown'
import { getLatestCourseChat, streamCourseChat, type ChatCitation, type ChatReply } from '../api'
import { courseChatSchema, type CourseChatFormValues } from '../lib/formSchemas'
import { sourceLabel } from '../lib/sourceLabel'
import { mathRehypePlugins, mathRemarkPlugins } from '../lib/markdownMath'

interface Turn {
  question: string
  answer: string
  citations: ChatCitation[]
  error?: string
}

// Saved messages back into turns: a question, then the answer to it. A question whose answer
// failed keeps an empty answer.
function turnsFrom(messages: ChatReply[]): Turn[] {
  const turns: Turn[] = []
  for (const message of messages) {
    if (message.role === 'user') turns.push({ question: message.content, answer: '', citations: [] })
    else if (turns.length && !turns[turns.length - 1].answer) {
      turns[turns.length - 1] = { ...turns[turns.length - 1], answer: message.content, citations: message.citations }
    } else turns.push({ question: '', answer: message.content, citations: message.citations })
  }
  return turns
}

// Questions about the course's documents, with the answer appearing as it is written. Opens on
// the conversation used last, so reloading the page does not lose it.
export function CourseChat({ courseId }: { courseId: string }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const sessionId = useRef<string | undefined>(undefined)
  const leaving = useRef<AbortController | null>(null)
  useEffect(() => () => leaving.current?.abort(), [])
  useEffect(() => {
    // Another course is a new mount (key={courseId} on the page), so the state starts fresh.
    const controller = new AbortController()
    getLatestCourseChat(courseId, controller.signal)
      .then((latest) => {
        sessionId.current = latest.session_id ?? undefined
        setTurns(turnsFrom(latest.messages))
      })
      .catch((err) => {
        // Sending still works: the next question starts a new conversation.
        if (!controller.signal.aborted) setLoadError(err instanceof Error && !(err instanceof TypeError)
          ? err.message : 'Không tải được cuộc trò chuyện trước.')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [courseId])
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

  function startOver() {
    sessionId.current = undefined
    setTurns([])
    setLoadError('')
  }

  return <section className="mt-6 rounded-[3px] border border-gray-200 bg-white p-6">
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-sm font-semibold">Hỏi đáp về tài liệu</h3>
      {turns.length > 0 && <button type="button" onClick={startOver} disabled={sending}
        className="text-xs text-gray-600 underline disabled:opacity-60">Cuộc trò chuyện mới</button>}
    </div>
    {loading && <p className="mt-3 text-sm text-gray-500">Đang tải cuộc trò chuyện...</p>}
    {loadError && <p className="mt-3 text-sm text-gray-500">{loadError}</p>}
    <div role="log" aria-label="Hội thoại" className="mt-3 flex flex-col gap-3">
      {turns.map((turn, index) => {
        const writing = sending && index === turns.length - 1
        return <div key={index} className="text-sm" aria-busy={writing}>
          {turn.question && <p className="font-medium">{turn.question}</p>}
          {turn.answer && <div className="mt-1 [&_li]:ml-5 [&_ol]:list-decimal [&_p]:mb-2 [&_strong]:font-semibold [&_ul]:list-disc">
            <Markdown remarkPlugins={mathRemarkPlugins} rehypePlugins={mathRehypePlugins}>{turn.answer}</Markdown>
          </div>}
          {writing && !turn.answer && <p className="mt-1 text-gray-500">Đang tìm trong tài liệu...</p>}
          {turn.citations.length > 0 && <p className="mt-1 text-xs text-gray-500">
            Nguồn: {[...new Set(turn.citations.map((c) => sourceLabel(c.source_ref)))].join('; ')}
          </p>}
          {turn.error && <p role="alert" className="mt-1 text-red-700">{turn.error}</p>}
        </div>
      })}
    </div>
    {/* oxlint-disable-next-line react/refs -- handleSubmit is react-hook-form's submit wrapper, not a ref read during render */}
    <form onSubmit={handleSubmit(send)} noValidate className="mt-3 flex flex-col gap-2">
      <label htmlFor="course-chat-message" className="text-sm">Câu hỏi của bạn</label>
      <textarea id="course-chat-message" maxLength={4000} rows={3} disabled={sending || loading}
        {...register('message')} className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta" />
      {errors.message && <p className="text-xs text-red-600">{errors.message.message}</p>}
      <button type="submit" disabled={sending || loading || blank}
        className="w-fit rounded-[3px] bg-terracotta px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
        {sending ? 'Đang trả lời...' : 'Gửi câu hỏi'}
      </button>
    </form>
  </section>
}
