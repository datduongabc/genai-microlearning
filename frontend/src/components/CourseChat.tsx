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

  // On the course page the log scrolls inside its column, so it follows the answer being written,
  // unless the reader has scrolled up to read something earlier.
  const log = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  useEffect(() => {
    const el = log.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [turns])

  const updateLast = (change: (turn: Turn) => Turn) =>
    setTurns((previous) => [...previous.slice(0, -1), change(previous[previous.length - 1])])

  async function send({ message }: CourseChatFormValues) {
    following.current = true
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

  return <section className="chat-panel" aria-labelledby="course-chat-title">
    <div className="chat-head">
      <h2 id="course-chat-title" className="course-section-title">Hỏi đáp về tài liệu</h2>
      {turns.length > 0 && <button type="button" onClick={startOver} disabled={sending}
        className="link-action small-action">Cuộc trò chuyện mới</button>}
    </div>
    {loading && <p className="chat-note">Đang tải cuộc trò chuyện...</p>}
    {loadError && <p className="chat-note">{loadError}</p>}
    {!loading && !loadError && turns.length === 0 && <p className="chat-note">
      Câu trả lời lấy từ tài liệu của môn học, kèm slide hoặc trang nguồn.
    </p>}
    <div role="log" aria-label="Hội thoại" className="chat-log" ref={log}
      onScroll={(e) => { const el = e.currentTarget; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48 }}>
      {turns.map((turn, index) => {
        const writing = sending && index === turns.length - 1
        return <div key={index} className="chat-turn" aria-busy={writing}>
          {turn.question && <p className="chat-question">{turn.question}</p>}
          {turn.answer && <div className="chat-answer">
            <Markdown remarkPlugins={mathRemarkPlugins} rehypePlugins={mathRehypePlugins}>{turn.answer}</Markdown>
          </div>}
          {writing && !turn.answer && <p className="chat-note">Đang tìm trong tài liệu...</p>}
          {turn.citations.length > 0 && <p className="chat-sources">
            Nguồn: {[...new Set(turn.citations.map((c) => sourceLabel(c.source_ref)))].join('; ')}
          </p>}
          {turn.error && <p role="alert" className="danger-text">{turn.error}</p>}
        </div>
      })}
    </div>
    {/* oxlint-disable-next-line react/refs -- handleSubmit is react-hook-form's submit wrapper, not a ref read during render */}
    <form onSubmit={handleSubmit(send)} noValidate className="chat-form">
      <label htmlFor="course-chat-message" className="chat-label">Câu hỏi của bạn</label>
      <textarea id="course-chat-message" maxLength={4000} rows={3} disabled={sending || loading}
        {...register('message')} className="ui-input w-full" />
      {errors.message && <p className="danger-text text-[13px]">{errors.message.message}</p>}
      <button type="submit" disabled={sending || loading || blank}
        className="ui-button primary w-fit">
        {sending ? 'Đang trả lời...' : 'Gửi câu hỏi'}
      </button>
    </form>
  </section>
}
