import { useEffect, useState } from 'react'
import { Check, FileWarning, ThumbsDown } from 'lucide-react'
import { listChatFeedback, type ChatFeedbackItem } from '../api'
import { sourceLabel } from '../lib/sourceLabel'

const VERDICT = {
  helpful: { label: 'Hữu ích', Icon: Check },
  not_right: { label: 'Chưa đúng', Icon: ThumbsDown },
  wrong_citation: { label: 'Sai trích dẫn', Icon: FileWarning },
}

// For the course's lecturers: the chat answers readers said something about, without who they were. Problems
// first, so a lecturer sees what to fix in the documents; nothing is shown while there is no feedback.
export function ChatFeedbackList({ courseId }: { courseId: string }) {
  const [items, setItems] = useState<ChatFeedbackItem[] | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    listChatFeedback(courseId).then((loaded) => { if (live) setItems(loaded) }, () => { if (live) setFailed(true) })
    return () => { live = false }
  }, [courseId])

  if (failed) return <p className="course-hint">Không tải được phản hồi của sinh viên về chat.</p>
  if (!items?.length) return null
  const problems = items.filter((i) => i.feedback !== 'helpful')
  const ordered = [...problems, ...items.filter((i) => i.feedback === 'helpful')]
  return <details className="feedback-list" open={problems.length > 0}>
    <summary>
      Phản hồi của sinh viên về chat: {problems.length} chưa đúng, {items.length - problems.length} hữu ích
    </summary>
    <p className="course-hint">Không kèm tên người hỏi. Câu trả lời sai thường do tài liệu thiếu hoặc nói chưa rõ.</p>
    <ul>
      {ordered.map((item) => {
        const { label, Icon } = VERDICT[item.feedback]
        return <li key={item.message_id} className="feedback-item">
          <p className="feedback-verdict" data-feedback={item.feedback}><Icon aria-hidden="true" />{label}
            <span className="feedback-date">{new Date(item.feedback_at).toLocaleString('vi-VN')}</span></p>
          {item.question && <p className="feedback-question">{item.question}</p>}
          <p className="feedback-answer">{item.answer}</p>
          {item.flagged_citations.length > 0 && <p className="feedback-flagged">
            Trích dẫn bị báo sai: {item.flagged_citations.map((n) =>
              `[${n}] ${item.citations[n - 1] ? sourceLabel(item.citations[n - 1].source_ref) : ''}`.trim()).join('; ')}
          </p>}
          {item.note && <p className="feedback-note">Ghi chú: {item.note}</p>}
        </li>
      })}
    </ul>
  </details>
}
