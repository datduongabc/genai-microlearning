import { useState } from 'react'
import { Check, FileWarning, ThumbsDown, ThumbsUp } from 'lucide-react'
import { giveChatFeedback, type ChatFeedback as Verdict, type ChatReply } from '../api'

const LABEL: Record<Verdict, string> = {
  helpful: 'Hữu ích',
  not_right: 'Chưa đúng',
  wrong_citation: 'Sai trích dẫn',
}
const ICON = { helpful: ThumbsUp, not_right: ThumbsDown, wrong_citation: FileWarning }

// Under an answer: was it helpful, not right, or citing the wrong source. "Helpful" is sent at once; the other
// two open a short form for which citation and why, both optional. Pressing the chosen one again takes it back.
export function ChatFeedback({ courseId, messageId, citationCount, given, givenCitations, onSaved }: {
  courseId: string
  messageId: string
  citationCount: number
  given: Verdict | null
  givenCitations: number[]
  onSaved: (reply: ChatReply) => void
}) {
  const [open, setOpen] = useState<Verdict | null>(null)
  const [flagged, setFlagged] = useState<number[]>(givenCitations)
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)

  async function send(feedback: Verdict | null, extra: { note?: string; citations?: number[] } = {}) {
    setSending(true)
    setStatus(null)
    try {
      onSaved(await giveChatFeedback(courseId, messageId, { feedback, ...extra }))
      setOpen(null)
      setNote('')
      setStatus({ ok: true, text: feedback ? 'Cảm ơn, đã gửi phản hồi.' : 'Đã bỏ phản hồi.' })
    } catch (err) {
      setStatus({ ok: false, text: err instanceof Error && !(err instanceof TypeError) ? err.message : 'Không gửi được phản hồi, vui lòng thử lại.' })
    } finally {
      setSending(false)
    }
  }

  function choose(verdict: Verdict) {
    if (given === verdict) return send(null)
    if (verdict === 'helpful') return send('helpful')
    setOpen(open === verdict ? null : verdict)
    setStatus(null)
  }

  const kinds: Verdict[] = citationCount > 0 ? ['helpful', 'not_right', 'wrong_citation'] : ['helpful', 'not_right']
  return <div className="chat-feedback">
    <div className="chat-feedback-row" role="group" aria-label="Phản hồi về câu trả lời">
      {kinds.map((verdict) => {
        const Icon = ICON[verdict]
        return <button key={verdict} type="button" className="chat-feedback-button" disabled={sending}
          aria-pressed={given === verdict} aria-expanded={verdict === 'helpful' ? undefined : open === verdict}
          onClick={() => choose(verdict)}>
          {given === verdict ? <Check aria-hidden="true" /> : <Icon aria-hidden="true" />}{LABEL[verdict]}
        </button>
      })}
    </div>
    {open && <form className="chat-feedback-form" onSubmit={(e) => {
      e.preventDefault()
      send(open, { note: note.trim() || undefined, citations: open === 'wrong_citation' ? flagged : [] })
    }}>
      {open === 'wrong_citation' && <fieldset>
        <legend>Trích dẫn nào sai?</legend>
        {Array.from({ length: citationCount }, (_, i) => i + 1).map((n) => (
          <label key={n} className="chat-feedback-check">
            <input type="checkbox" checked={flagged.includes(n)}
              onChange={(e) => setFlagged(e.target.checked ? [...flagged, n] : flagged.filter((x) => x !== n))} />
            Nguồn {n}
          </label>
        ))}
      </fieldset>}
      <label htmlFor={`feedback-note-${messageId}`} className="chat-label">Điều gì chưa đúng? (không bắt buộc)</label>
      <textarea id={`feedback-note-${messageId}`} rows={2} maxLength={1000} value={note}
        onChange={(e) => setNote(e.target.value)} className="ui-input w-full" />
      <p className="chat-note">Câu hỏi và câu trả lời này được gửi cho giảng viên, không kèm tên bạn.</p>
      <div className="chat-feedback-actions">
        <button type="submit" className="ui-button primary" disabled={sending}>
          {sending ? 'Đang gửi…' : 'Gửi phản hồi'}
        </button>
        <button type="button" className="link-action" onClick={() => setOpen(null)}>Hủy</button>
      </div>
    </form>}
    {status && <p role={status.ok ? 'status' : 'alert'} className={status.ok ? 'chat-note' : 'danger-text text-sm'}>{status.text}</p>}
  </div>
}
