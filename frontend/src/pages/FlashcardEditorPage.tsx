import { confirmAction } from '../lib/confirm'
import { useCourseCrumbs } from '../lib/useCourseCrumbs'
import { notifyDone, notifyFailed } from '../lib/notify'
import axios from 'axios'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  apiErrorMessage,
  deleteFlashcardSet,
  getFlashcardSet,
  MAX_FLASHCARDS,
  publishFlashcardSet,
  reviseFlashcardSet,
  saveFlashcards,
  type FlashcardSetDetail,
} from '../api'
import { AppShell } from '../components/AppShell'
import { AutoGrowTextarea } from '../components/AutoGrowTextarea'
import { CitationMarkers } from '../components/CitationMarkers'
import { editorTitle } from '../lib/editorTitle'
import { useLeaveGuard } from '../lib/useLeaveGuard'

const POLL_INTERVAL_MS = 3000
const NO_SOURCE = 'Chưa có nguồn'

// The one card open for editing: `index` is its place in the saved set, or null for a card being added.
interface Editing {
  index: number | null
  front: string
  back: string
}

export function FlashcardEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [saved, setSaved] = useState<FlashcardSetDetail | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<'save' | 'publish' | 'delete' | 'revise' | null>(null)
  const [request, setRequest] = useState('')

  // The first load, and the retry after a failed one. A result that arrives after the page has
  // gone is dropped, and state is set only from promise callbacks.
  useEffect(() => {
    let cancelled = false
    if (!id) return
    getFlashcardSet(id).then(
      (set) => {
        if (cancelled) return
        setSaved(set)
        setLoading(false)
      },
      (err) => {
        if (cancelled) return
        // Only a 404 means the set is gone; anything else is a failed load that a retry may fix.
        if (axios.isAxiosError(err) && err.response?.status === 404) setMissing(true)
        else setLoadFailed(true)
        setLoading(false)
      },
    )
    return () => {
      cancelled = true
    }
  }, [id, attempt])

  // A set being generated is a job in the worker, so the server's status is the truth.
  const generating = saved?.status === 'generating'
  useEffect(() => {
    if (!generating || !id) return
    const timer = setInterval(async () => {
      try {
        setSaved(await getFlashcardSet(id))
      } catch {
        // transient failure: the next tick retries
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [generating, id])

  const original = editing?.index != null ? saved?.cards[editing.index] : undefined
  const unsaved = editing !== null && (editing.front !== (original?.front ?? '') || editing.back !== (original?.back ?? ''))
  const allowLeave = useLeaveGuard(unsaved)

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  function back(courseId: string) {
    navigate(`/courses/${courseId}`)
  }

  const crumbs = useCourseCrumbs(saved?.course_id)

  if (loading) {
    return <AppShell title="Flashcard" crumbs={crumbs}><p className="course-empty">Đang tải…</p></AppShell>
  }
  if (missing) {
    return (
      <AppShell title="Flashcard" crumbs={crumbs}>
        <p className="text-sm danger-text">Không tìm thấy bộ flashcard.</p>
        <Link to="/courses" className="link-action">Về danh sách môn học</Link>
      </AppShell>
    )
  }
  if (loadFailed || !saved || !id) {
    return (
      <AppShell title="Flashcard" crumbs={crumbs}>
        <p role="alert" className="text-sm danger-text">Không tải được bộ flashcard.</p>
        <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
      </AppShell>
    )
  }

  const setId = id
  const draftVersion = saved.draft_version
  const courseId = saved.course_id
  const titleProps = editorTitle('Flashcard', saved.focus)
  const published = saved.published_at !== null
  const cards = saved.cards
  const full = cards.length >= MAX_FLASHCARDS
  const kept = (list: FlashcardSetDetail['cards']) => list.map(({ front, back, citations }) => ({ front, back, citations }))

  // Every change is saved at once, as in the module editor: there is no unsaved set to lose or to forget to save.
  async function store(list: ReturnType<typeof kept>, done: string) {
    setBusy('save')
    try {
      setSaved(await saveFlashcards(setId, draftVersion, list))
      setEditing(null)
      notifyDone(done)
    } catch (err) {
      notifyFailed(apiErrorMessage(err, 'Lưu thẻ thất bại'))
    } finally {
      setBusy(null)
    }
  }

  function saveEditing(open: Editing) {
    const card = { front: open.front.trim(), back: open.back.trim() }
    const list = kept(cards)
    if (open.index === null) list.push({ ...card, citations: [] })
    // Editing the words keeps the card's sources.
    else list[open.index] = { ...list[open.index], ...card }
    void store(list, open.index === null ? 'Đã thêm thẻ.' : 'Đã lưu thẻ.')
  }

  async function removeCard(index: number) {
    if (!await confirmAction({ title: `Xóa thẻ ${index + 1}?`, description: 'Không thể khôi phục.', action: 'Xóa thẻ', destructive: true })) return
    void store(kept(cards).filter((_, i) => i !== index), 'Đã xóa thẻ.')
  }

  async function revise() {
    setBusy('revise')
    try {
      const { reply, changed, ...set } = await reviseFlashcardSet(setId, draftVersion, request.trim())
      setSaved(set)
      setRequest('')
      if (changed) notifyDone(reply || 'Đã sửa bộ thẻ.')
      else notifyFailed(reply || 'Bộ thẻ không thay đổi.')
    } catch (err) {
      notifyFailed(apiErrorMessage(err, 'Sửa bộ thẻ thất bại. Vui lòng thử lại.'))
    } finally {
      setBusy(null)
    }
  }

  async function togglePublished() {
    setBusy('publish')
    try {
      setSaved(await publishFlashcardSet(setId, !published))
      notifyDone(published ? 'Đã ngừng giao bộ flashcard.' : 'Đã giao bộ flashcard cho sinh viên.')
    } catch (err) {
      notifyFailed(apiErrorMessage(err, published ? 'Ngừng giao thất bại' : 'Giao bộ flashcard thất bại'))
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!await confirmAction({ title: 'Xóa bộ flashcard này?', description: 'Không thể khôi phục.', action: 'Xóa bộ flashcard', destructive: true })) return
    setBusy('delete')
    try {
      await deleteFlashcardSet(setId)
      // What was being edited is gone, so its unsaved edits are not worth a question.
      allowLeave()
      notifyDone('Đã xóa bộ flashcard.')
      navigate(`/courses/${courseId}`)
    } catch (err) {
      notifyFailed(apiErrorMessage(err, 'Xóa bộ flashcard thất bại'))
      setBusy(null)
    }
  }

  const deleteButton = (
    <button type="button" onClick={remove} disabled={busy !== null || generating} className="ui-button danger ml-auto">
      {busy === 'delete' ? 'Đang xóa…' : 'Xóa bộ flashcard'}
    </button>
  )

  if (generating) {
    return (
      <AppShell {...titleProps} crumbs={crumbs}>
        <button type="button" onClick={() => back(saved.course_id)} className="link-action">Về trang môn học</button>
        <p role="status" className="course-empty">Đang tạo flashcard…</p>
      </AppShell>
    )
  }

  if (saved.status === 'failed') {
    return (
      <AppShell {...titleProps} crumbs={crumbs}>
        <p role="alert" className="text-sm danger-text">
          Tạo flashcard thất bại. Xóa bộ này rồi tạo lại từ trang môn học.
        </p>
        <div className="mt-4 flex gap-4">
          <button type="button" onClick={() => back(saved.course_id)} className="link-action">Về trang môn học</button>
          {deleteButton}
        </div>
      </AppShell>
    )
  }

  const editor = (open: Editing, number: number) => (
    <div className="flashcard-form">
      <label className="flashcard-field">
        Mặt trước (câu hỏi)
        <AutoGrowTextarea aria-label={`Mặt trước thẻ ${number}`} value={open.front} autoFocus
          onChange={(e) => setEditing({ ...open, front: e.target.value })} maxLength={1000} rows={2} className="ui-input w-full" />
      </label>
      <label className="flashcard-field">
        Mặt sau (đáp án)
        <AutoGrowTextarea aria-label={`Mặt sau thẻ ${number}`} value={open.back}
          onChange={(e) => setEditing({ ...open, back: e.target.value })} maxLength={4000} rows={3} className="ui-input w-full" />
      </label>
      <div className="flashcard-actions">
        <button type="button" onClick={() => saveEditing(open)} className="ui-button primary"
          disabled={busy !== null || open.front.trim() === '' || open.back.trim() === ''}>
          {busy === 'save' ? 'Đang lưu…' : 'Lưu'}
        </button>
        <button type="button" onClick={() => setEditing(null)} disabled={busy !== null} className="ui-button">Hủy</button>
      </div>
    </div>
  )

  return (
    <AppShell {...titleProps} crumbs={crumbs}>
      <div className="flashcard-editor">
        <ol className="flashcard-list" aria-label="Các thẻ">
          {cards.map((c, index) => {
            // A model card always cites, so "Chưa có nguồn" (older saves) was only ever on the lecturer's own card.
            const warning = c.flag === NO_SOURCE ? null : c.flag
            const open = editing?.index === index ? editing : null
            return (
              <li key={index} className="flashcard-item">
                <div className="flashcard-head">
                  <span className="part-label">Thẻ {index + 1}</span>
                  {!open && (
                    <div className="flashcard-item-actions">
                      <button type="button" onClick={() => setEditing({ index, front: c.front, back: c.back })}
                        disabled={busy !== null || editing !== null} aria-label={`Sửa thẻ ${index + 1}`} className="link-action small-action">
                        Sửa
                      </button>
                      <button type="button" onClick={() => removeCard(index)} disabled={busy !== null || editing !== null}
                        aria-label={`Xóa thẻ ${index + 1}`} className="link-action danger small-action">
                        Xóa
                      </button>
                    </div>
                  )}
                </div>
                {warning && <p className="doc-warning">{warning}</p>}
                {open ? editor(open, index + 1) : (
                  <dl className="flashcard-sides">
                    <dt>Mặt trước</dt>
                    <dd className="flashcard-front">{c.front}</dd>
                    <dt>Mặt sau</dt>
                    <dd>{c.back}</dd>
                  </dl>
                )}
                {!open && <CitationMarkers sources={c.sources} citations={c.citations} label={`Nguồn của thẻ ${index + 1}`} />}
              </li>
            )
          })}
          {editing?.index === null && (
            <li className="flashcard-item">
              <span className="part-label">Thẻ mới</span>
              {editor(editing, cards.length + 1)}
            </li>
          )}
        </ol>

        <section className="module-ai-edit flashcard-revise" aria-labelledby="flashcard-revise-title">
          <h2 id="flashcard-revise-title" className="module-part-title">Yêu cầu chỉnh sửa</h2>
          <textarea id="flashcard-revise" aria-labelledby="flashcard-revise-title" value={request} onChange={(e) => setRequest(e.target.value)} maxLength={2000} rows={3}
            disabled={busy !== null}
            className="ui-input mt-2 w-full" />
          {/* The request rewrites the saved set, so it waits for the card open for editing. */}
          <button type="button" onClick={revise} disabled={busy !== null || editing !== null || request.trim() === ''}
            className="ui-button primary mt-2 w-fit">
            {busy === 'revise' ? 'Đang sửa…' : 'Gửi yêu cầu'}
          </button>
        </section>

        <button type="button" onClick={() => setEditing({ index: null, front: '', back: '' })}
          disabled={full || busy !== null || editing !== null} className="ui-button">
          Thêm thẻ
        </button>

        <div className="flashcard-actions flashcard-final">
          <button
            type="button"
            onClick={togglePublished}
            // A card open for editing would not reach the learners, and an empty set has nothing to learn.
            disabled={busy !== null || (!published && (editing !== null || cards.length === 0))}
            className="ui-button primary"
          >
            {busy === 'publish' ? 'Đang xử lý…' : published ? 'Ngừng giao' : 'Giao cho sinh viên'}
          </button>
          {deleteButton}
        </div>
      </div>
    </AppShell>
  )
}
