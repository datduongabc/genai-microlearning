import { confirmAction } from '../lib/confirm'
import { notifyDone, notifyFailed } from '../lib/notify'
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
import { AutoGrowTextarea } from '../components/AutoGrowTextarea'
import { PassagePicker } from '../components/PassagePicker'
import { CitationMarkers } from '../components/CitationMarkers'
import { editorTitle } from '../lib/editorTitle'
import { flashcardSpent } from '../lib/spent'
import { useLeaveGuard } from '../lib/useLeaveGuard'

const POLL_INTERVAL_MS = 3000
const NO_SOURCE = 'Chưa có nguồn'

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

  const unsaved = saved !== null && saved.status === 'draft' && !sameCards(cards, saved.cards)
  const allowLeave = useLeaveGuard(unsaved)

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  function back(courseId: string) {
    navigate(`/courses/${courseId}`)
  }

  if (loading) {
    return <AppShell title="Flashcard"><p className="course-empty">Đang tải…</p></AppShell>
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
  const draftVersion = saved.draft_version
  const courseId = saved.course_id
  const titleProps = editorTitle('Flashcard', saved.focus)
  const published = saved.published_at !== null
  const spent = flashcardSpent(saved.cost)
  const dirty = !sameCards(cards, saved.cards)
  const complete = cards.every((c) => c.front.trim() !== '' && c.back.trim() !== '')
  const full = cards.length >= MAX_FLASHCARDS

  function edit(key: number, change: Partial<FlashcardCardEdit>) {
    setCards((list) => list.map((c) => (c.key === key ? { ...c, ...change } : c)))
  }

  async function save() {
    setBusy('save')
    try {
      const set = await saveFlashcards(setId, draftVersion, cards.map(({ front, back, citations }) => ({ front, back, citations })))
      setSaved(set)
      setCards(toDrafts(set, nextKey))
      notifyDone('Đã lưu.')
    } catch (err) {
      notifyFailed(apiErrorMessage(err, 'Lưu thẻ thất bại'))
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
    <button type="button" onClick={remove} disabled={busy !== null || generating} className="link-action danger">
      {busy === 'delete' ? 'Đang xóa…' : 'Xóa bộ flashcard'}
    </button>
  )

  if (generating) {
    return (
      <AppShell {...titleProps}>
        <button type="button" onClick={() => back(saved.course_id)} className="link-action">Quay lại</button>
        <p role="status" className="course-empty">Đang tạo flashcard…</p>
      </AppShell>
    )
  }

  if (saved.status === 'failed') {
    return (
      <AppShell {...titleProps}>
        <p role="alert" className="text-sm danger-text">
          Tạo flashcard thất bại. Xóa bộ này rồi tạo lại từ trang môn học.
        </p>
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
            {published ? 'Đã giao cho sinh viên' : 'Bản nháp'}
          </span>
          <span className="flashcard-count">{cards.length} thẻ (tối đa {MAX_FLASHCARDS})</span>
          {spent && <span className="flashcard-count">{spent}</span>}
        </div>

        <ol className="flashcard-list" aria-label="Các thẻ">
          {cards.map((c, index) => {
            // The server's "no source" flag is out of date once the lecturer has picked a passage.
            const flag = c.flag === NO_SOURCE && c.citations.length > 0 ? null : c.flag
            const warning = flag ?? (c.citations.length === 0 ? NO_SOURCE : null)
            return (
              <li key={c.key} className="flashcard-item">
                <div className="flashcard-head">
                  <span className="part-label">Thẻ {index + 1}</span>
                  <button
                    type="button"
                    onClick={() => { setCards((list) => list.filter((x) => x.key !== c.key)) }}
                    aria-label={`Xóa thẻ ${index + 1}`}
                    className="link-action danger small-action"
                  >
                    Xóa thẻ
                  </button>
                </div>
                {warning && <p className="doc-warning">{warning}</p>}
                <label className="flashcard-field">
                  Mặt trước
                  <AutoGrowTextarea
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
                  <AutoGrowTextarea
                    aria-label={`Mặt sau thẻ ${index + 1}`}
                    value={c.back}
                    onChange={(e) => edit(c.key, { back: e.target.value })}
                    maxLength={4000}
                    rows={3}
                    className="ui-input w-full"
                  />
                </label>
                <CitationMarkers sources={c.sources} citations={c.citations} label={`Nguồn của thẻ ${index + 1}`} />
                <PassagePicker
                  documentId={saved.document_id}
                  name={`thẻ ${index + 1}`}
                  citations={c.citations}
                  known={c.sources}
                  onChange={(citations, picked) => {
                    setCards((list) => list.map((x) => (x.key === c.key
                      ? { ...x, citations, sources: picked ? [...x.sources, picked] : x.sources }
                      : x)))
                  }}
                />
              </li>
            )
          })}
        </ol>

        <button
          type="button"
          onClick={() => { setCards((list) => [...list, { key: nextKey(), front: '', back: '', citations: [], flag: null, sources: [] }]) }}
          disabled={full}
          className="ui-button"
        >
          Thêm thẻ
        </button>
        {full && <p className="course-hint">Một bộ có tối đa {MAX_FLASHCARDS} thẻ.</p>}

        {!complete && <p className="mt-3 text-sm danger-text">Mỗi thẻ cần cả mặt trước và mặt sau.</p>}
        {dirty && complete && (
          <p className="course-hint">{published ? 'Lưu để cập nhật cho sinh viên.' : 'Lưu thay đổi trước khi giao.'}</p>
        )}

        <div className="flashcard-actions">
          <button type="button" onClick={save} disabled={!dirty || !complete || busy !== null} className="ui-button primary">
            {busy === 'save' ? 'Đang lưu…' : 'Lưu thay đổi'}
          </button>
          <button
            type="button"
            onClick={togglePublished}
            // An unsaved edit would not reach the learners, and an empty set has nothing to learn.
            disabled={busy !== null || (!published && (dirty || cards.length === 0))}
            className="ui-button"
          >
            {busy === 'publish' ? 'Đang xử lý…' : published ? 'Ngừng giao' : 'Giao cho sinh viên'}
          </button>
          {deleteButton}
        </div>
      </div>
    </AppShell>
  )
}
