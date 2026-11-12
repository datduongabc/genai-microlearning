import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  apiErrorMessage,
  deleteFlashcardSet,
  getFlashcardSet,
  MAX_FLASHCARDS,
  publishFlashcardSet,
  saveFlashcards,
  type FlashcardCardEdit,
  type FlashcardSource,
  type FlashcardSetDetail,
} from '../api'
import { AppShell } from '../components/AppShell'

const POLL_INTERVAL_MS = 3000
const NO_SOURCE = 'Chưa có nguồn'
const MAX_TITLE_LENGTH = 60
const LEAVE_PROMPT = 'Các thay đổi chưa lưu sẽ mất. Rời trang?'

// A card as the lecturer is editing it. `key` keeps a textarea with its card when another card
// is removed; `flag` is the server's, which only changes when the set is saved.
interface Draft extends FlashcardCardEdit {
  key: number
  flag: string | null
  // Kept with the card so its passages stay visible while it is edited; an edit never changes them.
  sources: FlashcardSource[]
}

function toDrafts(set: FlashcardSetDetail, nextKey: () => number): Draft[] {
  return set.cards.map((c) => ({ key: nextKey(), front: c.front, back: c.back, citations: c.citations, flag: c.flag, sources: c.sources }))
}

function sameCards(a: Draft[], b: FlashcardSetDetail['cards']): boolean {
  return a.length === b.length && a.every((d, i) =>
    d.front === b[i].front && d.back === b[i].back
      && d.citations.length === b[i].citations.length && d.citations.every((c, j) => c === b[i].citations[j]))
}

export function FlashcardEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [saved, setSaved] = useState<FlashcardSetDetail | null>(null)
  const [cards, setCards] = useState<Draft[]>([])
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<'save' | 'publish' | 'delete' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const keyCounter = useRef(0)
  const nextKey = useCallback(() => keyCounter.current++, [])

  // The first load, and the retry after a failed one. A result that arrives after the page has
  // gone is dropped, and state is set only from promise callbacks.
  useEffect(() => {
    let cancelled = false
    if (!id) return
    getFlashcardSet(id).then(
      (set) => {
        if (cancelled) return
        setSaved(set)
        setCards(toDrafts(set, nextKey))
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
  }, [id, attempt, nextKey])

  // A set being generated is a job in the worker, so the server's status is the truth.
  const generating = saved?.status === 'generating'
  useEffect(() => {
    if (!generating || !id) return
    const timer = setInterval(async () => {
      try {
        const set = await getFlashcardSet(id)
        setSaved(set)
        setCards(toDrafts(set, nextKey))
      } catch {
        // transient failure: the next tick retries
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [generating, id, nextKey])

  // The router in use is not a data router, so useBlocker is unavailable: a closed tab or reload is
  // caught here, and the back button below asks for itself.
  const unsaved = saved !== null && saved.status === 'draft' && !sameCards(cards, saved.cards)
  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  function back(courseId: string) {
    if (unsaved && !window.confirm(LEAVE_PROMPT)) return
    navigate(`/courses/${courseId}`)
  }

  if (loading) {
    return <AppShell title="Flashcard"><p className="course-empty">Đang tải...</p></AppShell>
  }
  if (missing) {
    return (
      <AppShell title="Flashcard">
        <p className="text-sm danger-text">Không tìm thấy bộ flashcard.</p>
        <Link to="/courses" className="link-action">Về danh sách môn học</Link>
      </AppShell>
    )
  }
  if (loadFailed || !saved || !id) {
    return (
      <AppShell title="Flashcard">
        <p role="alert" className="text-sm danger-text">Không tải được bộ flashcard.</p>
        <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
      </AppShell>
    )
  }

  const setId = id
  const courseId = saved.course_id
  const fullTitle = saved.focus ? `Flashcard: ${saved.focus}` : 'Flashcard'
  const title = fullTitle.length > MAX_TITLE_LENGTH ? `${fullTitle.slice(0, MAX_TITLE_LENGTH - 1).trimEnd()}…` : fullTitle
  const titleProps = { title, fullTitle: title === fullTitle ? undefined : fullTitle }
  const published = saved.published_at !== null
  const dirty = !sameCards(cards, saved.cards)
  const complete = cards.every((c) => c.front.trim() !== '' && c.back.trim() !== '')
  const full = cards.length >= MAX_FLASHCARDS

  function edit(key: number, change: Partial<FlashcardCardEdit>) {
    setNotice(null)
    setCards((list) => list.map((c) => (c.key === key ? { ...c, ...change } : c)))
  }

  async function save() {
    setBusy('save')
    setError(null)
    setNotice(null)
    try {
      const set = await saveFlashcards(setId, cards.map(({ front, back, citations }) => ({ front, back, citations })))
      setSaved(set)
      setCards(toDrafts(set, nextKey))
      setNotice('Đã lưu.')
    } catch (err) {
      setError(apiErrorMessage(err, 'Lưu thẻ thất bại'))
    } finally {
      setBusy(null)
    }
  }

  async function togglePublished() {
    setBusy('publish')
    setError(null)
    setNotice(null)
    try {
      setSaved(await publishFlashcardSet(setId, !published))
    } catch (err) {
      setError(apiErrorMessage(err, published ? 'Ngừng giao thất bại' : 'Giao bộ thẻ thất bại'))
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    if (!window.confirm('Xóa vĩnh viễn bộ flashcard này? Không thể khôi phục.')) return
    setBusy('delete')
    setError(null)
    try {
      await deleteFlashcardSet(setId)
      navigate(`/courses/${courseId}`)
    } catch (err) {
      setError(apiErrorMessage(err, 'Xóa bộ thẻ thất bại'))
      setBusy(null)
    }
  }

  const deleteButton = (
    <button type="button" onClick={remove} disabled={busy !== null || generating} className="link-action danger">
      {busy === 'delete' ? 'Đang xóa...' : 'Xóa bộ thẻ'}
    </button>
  )

  if (generating) {
    return (
      <AppShell {...titleProps}>
        <button type="button" onClick={() => back(saved.course_id)} className="link-action">Quay lại</button>
        <p role="status" className="course-empty">Đang sinh bộ thẻ...</p>
      </AppShell>
    )
  }

  if (saved.status === 'failed') {
    return (
      <AppShell {...titleProps}>
        <p role="alert" className="text-sm danger-text">
          Sinh bộ thẻ thất bại. Xóa bộ này rồi sinh lại từ trang môn học.
        </p>
        {error && <p role="alert" className="mt-3 text-sm danger-text">{error}</p>}
        <div className="mt-4 flex gap-4">
          <button type="button" onClick={() => back(saved.course_id)} className="link-action">Quay lại</button>
          {deleteButton}
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell {...titleProps}>
      <div className="flashcard-editor">
        <div className="flashcard-bar">
          <button type="button" onClick={() => back(saved.course_id)} className="link-action">Quay lại</button>
          <span className="status-pill" data-status={published ? 'embedded' : undefined}>
            {published ? 'Đã giao cho người học' : 'Bản nháp'}
          </span>
          <span className="flashcard-count">{cards.length}/{MAX_FLASHCARDS} thẻ</span>
        </div>

        <ol className="flashcard-list" aria-label="Các thẻ">
          {cards.map((c, index) => {
            const warning = c.flag ?? (c.citations.length === 0 ? NO_SOURCE : null)
            return (
              <li key={c.key} className="flashcard-item">
                <div className="flashcard-head">
                  <span className="part-label">Thẻ {index + 1}</span>
                  <button
                    type="button"
                    onClick={() => { setNotice(null); setCards((list) => list.filter((x) => x.key !== c.key)) }}
                    aria-label={`Xóa thẻ ${index + 1}`}
                    className="link-action danger small-action"
                  >
                    Xóa thẻ
                  </button>
                </div>
                {warning && <p className="doc-warning">{warning}</p>}
                <label className="flashcard-field">
                  Mặt trước
                  <textarea
                    aria-label={`Mặt trước thẻ ${index + 1}`}
                    value={c.front}
                    onChange={(e) => edit(c.key, { front: e.target.value })}
                    maxLength={1000}
                    rows={2}
                    className="ui-input w-full"
                  />
                </label>
                <label className="flashcard-field">
                  Mặt sau
                  <textarea
                    aria-label={`Mặt sau thẻ ${index + 1}`}
                    value={c.back}
                    onChange={(e) => edit(c.key, { back: e.target.value })}
                    maxLength={4000}
                    rows={3}
                    className="ui-input w-full"
                  />
                </label>
                {c.sources.length > 0 && (
                  <ul className="flashcard-sources" aria-label={`Nguồn của thẻ ${index + 1}`}>
                    {c.sources.map((source) => (
                      <li key={source.chunk_id}>
                        <details>
                          <summary>{source.locator || 'Đoạn nguồn'}</summary>
                          <p className="module-source-passage">{source.content}</p>
                        </details>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            )
          })}
        </ol>

        <button
          type="button"
          onClick={() => { setNotice(null); setCards((list) => [...list, { key: nextKey(), front: '', back: '', citations: [], flag: null, sources: [] }]) }}
          disabled={full}
          className="ui-button"
        >
          Thêm thẻ
        </button>
        {full && <p className="course-hint">Một bộ có tối đa {MAX_FLASHCARDS} thẻ.</p>}

        {!complete && <p className="mt-3 text-sm danger-text">Mỗi thẻ cần cả mặt trước và mặt sau.</p>}
        {dirty && complete && (
          <p className="course-hint">{published ? 'Lưu để cập nhật cho người học.' : 'Lưu thay đổi trước khi giao.'}</p>
        )}
        {notice && <p role="status" className="mt-3 text-sm success-text">{notice}</p>}
        {error && <p role="alert" className="mt-3 text-sm danger-text">{error}</p>}

        <div className="flashcard-actions">
          <button type="button" onClick={save} disabled={!dirty || !complete || busy !== null} className="ui-button primary">
            {busy === 'save' ? 'Đang lưu...' : 'Lưu thay đổi'}
          </button>
          <button
            type="button"
            onClick={togglePublished}
            // An unsaved edit would not reach the learners, and an empty set has nothing to learn.
            disabled={busy !== null || (!published && (dirty || cards.length === 0))}
            className="ui-button"
          >
            {busy === 'publish' ? 'Đang xử lý...' : published ? 'Ngừng giao' : 'Giao cho người học'}
          </button>
          {deleteButton}
        </div>
      </div>
    </AppShell>
  )
}
