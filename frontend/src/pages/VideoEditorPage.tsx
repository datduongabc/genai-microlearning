import axios from 'axios'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  apiErrorMessage,
  CHARS_PER_SECOND,
  deleteVideo,
  drawIllustrations,
  getVideo,
  IMAGE_MODEL_CHOICES,
  MAX_BULLETS,
  MAX_SCENES,
  publishVideo,
  renderVideo,
  saveVideoScenes,
  VOICE_CHOICES,
  type SceneEdit,
  type SceneKind,
  type VideoDetail,
  type VideoJob,
  type VideoScene,
  type VideoSource,
} from '../api'
import { AppShell } from '../components/AppShell'
import { RenderedVideo } from '../components/RenderedVideo'
import { ScenePreview, SlidePlaceholder } from '../components/ScenePreview'
import { SourcePassages } from '../components/SourcePassages'
import { formatMinutes } from '../lib/duration'
import { editorTitle } from '../lib/editorTitle'
import { createLimiter } from '../lib/limiter'
import { confirmLeave, useLeaveGuard } from '../lib/useLeaveGuard'

const POLL_INTERVAL_MS = 3000
const NO_SOURCE = 'Chưa có nguồn'
// At most two slides are asked for at once: the server renders two at a time per process.
const PREVIEWS_AT_ONCE = 2

const KIND_LABEL: Record<SceneKind, string> = {
  title: 'Mở đầu',
  illustration: 'Minh họa',
  formula: 'Công thức',
  code: 'Code',
  table: 'Bảng',
  summary: 'Tóm tắt',
}

const JOB_TEXT: Record<VideoJob['job_type'], string> = {
  video_script: 'Đang sinh lời thoại...',
  video_images: 'Đang vẽ hình...',
  video_render: 'Đang dựng video...',
}
// A job between being picked up and the next poll shows only in the status.
const STATUS_JOB: Partial<Record<VideoDetail['status'], VideoJob['job_type']>> = {
  generating: 'video_script',
  drawing: 'video_images',
  rendering: 'video_render',
}
const DONE_TEXT: Partial<Record<VideoJob['job_type'], string>> = {
  video_images: 'Đã vẽ xong hình.',
  video_render: 'Đã dựng xong video.',
}

// How the server labels the item a flag is about (app/api/videos.py _items).
const WHOLE_ITEM_LABEL: Partial<Record<SceneKind, string>> = { formula: 'Công thức', code: 'Code', table: 'Bảng' }
const ITEM_FLAG = /^(Ý \d+|Câu \d+|Công thức|Code|Bảng): (.*)$/s

// A bullet or a sentence as it is edited. `label` is the one it had in the loaded draft ("Ý 2"),
// which is how its stored flags name it, so they stay with it when items are moved or removed.
interface ItemDraft {
  key: number
  text: string
  citations: string[]
  label: string | null
}

interface SceneDraft {
  key: number
  // Its index in the loaded draft, sent back so its picture, voice and flags come with it.
  fromIndex: number | null
  kind: SceneKind
  title: string
  bullets: ItemDraft[]
  sentences: ItemDraft[]
  formula: string
  code: string
  // One row a line, cells split by "|".
  table: string
  imagePrompt: string
  // The stored flags the lecturer keeps, "Chưa có nguồn" aside: that one is worked out from the
  // citations, here as on the server, and cannot be dismissed.
  flags: string[]
  // Every passage the loaded scene cites, so a source unticked from one item can still be ticked again.
  sources: VideoSource[]
}

function itemDrafts(texts: string[], citations: string[][], prefix: string, nextKey: () => number): ItemDraft[] {
  return texts.map((text, i) => ({ key: nextKey(), text, citations: citations[i] ?? [], label: `${prefix} ${i + 1}` }))
}

function toDrafts(video: VideoDetail, nextKey: () => number): SceneDraft[] {
  return video.scenes.map((s, i) => ({
    key: nextKey(),
    fromIndex: i,
    kind: s.kind,
    title: s.title,
    bullets: itemDrafts(s.bullets, s.bullet_citations, 'Ý', nextKey),
    sentences: itemDrafts(s.narration ? s.narration.split('\n') : [], s.narration_citations, 'Câu', nextKey),
    formula: s.formula ?? '',
    code: s.code ?? '',
    table: (s.table ?? []).map((row) => row.join(' | ')).join('\n'),
    imagePrompt: s.image_prompt ?? '',
    flags: s.flags.filter((f) => !f.startsWith(`${NO_SOURCE}: `)),
    sources: s.sources,
  }))
}

function parseTable(text: string): string[][] | null {
  const rows = text.split('\n').filter((line) => line.trim()).map((line) => line.split('|').map((cell) => cell.trim()))
  return rows.length > 0 ? rows : null
}

const filled = (items: ItemDraft[]) => items.filter((item) => item.text.trim() !== '')

function toEdits(scenes: SceneDraft[]): SceneEdit[] {
  return scenes.map((s) => {
    // An emptied bullet or sentence is a removed one: the server takes no blank items.
    const bullets = filled(s.bullets)
    const sentences = filled(s.sentences)
    return {
      from_index: s.fromIndex,
      kind: s.kind,
      title: s.title.trim(),
      bullets: bullets.map((b) => b.text.trim()),
      bullet_citations: bullets.map((b) => b.citations),
      // Only the field of the scene's kind is kept by the server, so only that one is sent.
      formula: s.kind === 'formula' && s.formula.trim() ? s.formula : null,
      code: s.kind === 'code' && s.code.trim() ? s.code : null,
      table: s.kind === 'table' ? parseTable(s.table) : null,
      image_prompt: s.kind === 'illustration' && s.imagePrompt.trim() ? s.imagePrompt : null,
      narration: sentences.map((t) => t.text.trim()).join('\n'),
      narration_citations: sentences.map((t) => t.citations),
      ...(s.fromIndex === null ? {} : { flags: s.flags }),
    }
  })
}

const editsKey = (scenes: SceneDraft[]) => JSON.stringify(toEdits(scenes))

// The same count the server makes (app/generation/video_script.py estimated_seconds).
function estimatedSeconds(scenes: SceneDraft[]): number {
  return toEdits(scenes).reduce((sum, s) => sum + s.narration.length, 0) / CHARS_PER_SECOND
}

// A short stand-in for what a slide prints, so its preview is asked for again only when that changes.
function slideVersion(scene: VideoScene, index: number, picturesDrawn: number): string {
  const printed = JSON.stringify([index, scene.kind, scene.title, scene.bullets, scene.formula, scene.code, scene.table,
    scene.citations, scene.has_image, scene.image_outdated, scene.kind === 'illustration' ? picturesDrawn : 0])
  let hash = 5381
  for (let i = 0; i < printed.length; i++) hash = (hash * 33 + printed.charCodeAt(i)) | 0
  return (hash >>> 0).toString(36)
}

function flagsFor(flags: string[], label: string | null): string[] {
  if (label === null) return []
  return flags.filter((f) => f.startsWith(`${label}: `))
}

const sceneFlags = (flags: string[]) => flags.filter((f) => !ITEM_FLAG.test(f))
const flagMessage = (flag: string) => flag.match(ITEM_FLAG)?.[2] ?? flag

function formatUsd(value: number): string {
  return `$${value.toLocaleString('vi-VN', { maximumFractionDigits: 3 })}`
}

function FlagNote({ flag, dismissLabel, onDismiss }: { flag: string; dismissLabel: string; onDismiss: () => void }) {
  return (
    <div className="doc-warning video-flag">
      <span>{flagMessage(flag)}</span>
      <button type="button" onClick={onDismiss} aria-label={dismissLabel} className="link-action small-action">
        Bỏ đánh dấu
      </button>
    </div>
  )
}

function ItemEditor({ item, name, sources, flags, onText, onCitations, onDismiss, onRemove, rows, maxLength }: {
  item: ItemDraft
  // "ý 2 cảnh 1": the accessible names of the item's controls.
  name: string
  sources: VideoSource[]
  flags: string[]
  onText: (text: string) => void
  onCitations: (citations: string[]) => void
  onDismiss: (flag: string) => void
  onRemove: () => void
  rows: number
  maxLength: number
}) {
  const label = name.charAt(0).toUpperCase() + name.slice(1)
  const uncited = item.text.trim() !== '' && item.citations.length === 0
  const chosen = sources.filter((s) => item.citations.includes(s.chunk_id))
  return (
    <li className="video-item">
      <div className="video-item-row">
        <textarea
          aria-label={label}
          value={item.text}
          // One sentence is one line on the server, so a line break would split it in two.
          onChange={(e) => onText(e.target.value.replace(/\n/g, ' '))}
          maxLength={maxLength}
          rows={rows}
          className="ui-input w-full"
        />
        <button type="button" onClick={onRemove} aria-label={`Xóa ${name}`} className="link-action danger small-action">
          Xóa
        </button>
      </div>
      {uncited && <p className="doc-warning">{NO_SOURCE}</p>}
      {flags.map((flag) => (
        <FlagNote key={flag} flag={flag} dismissLabel={`Bỏ đánh dấu ${name}`} onDismiss={() => onDismiss(flag)} />
      ))}
      {sources.length > 0 && (
        <details className="video-item-sources">
          <summary>
            {chosen.length > 0 ? `Nguồn: ${chosen.map((s) => s.locator || 'Đoạn nguồn').join('; ')}` : 'Gắn nguồn'}
          </summary>
          <fieldset aria-label={`Nguồn của ${name}`}>
            {sources.map((source) => (
              <label key={source.chunk_id} className="video-source-choice" title={source.content}>
                <input
                  type="checkbox"
                  checked={item.citations.includes(source.chunk_id)}
                  onChange={(e) => onCitations(e.target.checked
                    ? [...item.citations, source.chunk_id]
                    : item.citations.filter((c) => c !== source.chunk_id))}
                />
                {source.locator || 'Đoạn nguồn'}
              </label>
            ))}
          </fieldset>
        </details>
      )}
    </li>
  )
}

export function VideoEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [saved, setSaved] = useState<VideoDetail | null>(null)
  const [scenes, setScenes] = useState<SceneDraft[]>([])
  // The draft the edits are made on: its version goes with the save, its edits tell what is unsaved.
  const [base, setBase] = useState<{ version: number; edits: string }>({ version: 0, edits: '[]' })
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<'save' | 'draw' | 'render' | 'publish' | 'delete' | 'reload' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // Goes up when a drawing job ends, so an illustration's slide is asked for again even when a
  // redraw changed nothing but the picture.
  const [picturesDrawn, setPicturesDrawn] = useState(0)
  const keyCounter = useRef(0)
  const nextKey = useCallback(() => keyCounter.current++, [])
  const limiter = useMemo(() => createLimiter(PREVIEWS_AT_ONCE), [])

  // What a poll compares with; refs, because the poll's callback outlives the render it was made in.
  const latest = useRef({ saved, scenes, base })
  useEffect(() => {
    latest.current = { saved, scenes, base }
  })

  // Takes a fresh draft as the one being edited, dropping any edit. A scene keeps the key of the
  // one shown in its place, so a save does not mount its slide again and fetch it for nothing.
  const adopt = useCallback((video: VideoDetail) => {
    const shown = latest.current.scenes
    const drafts = toDrafts(video, nextKey).map((d, i) => (i < shown.length ? { ...d, key: shown[i].key } : d))
    setSaved(video)
    setScenes(drafts)
    setBase({ version: video.draft_version, edits: editsKey(drafts) })
  }, [nextKey])

  // A detail from a poll or an action. The lecturer's unsaved edits are never replaced by it; a
  // draft nobody touched follows the server.
  const follow = useCallback((video: VideoDetail) => {
    const now = latest.current
    if (now.saved?.job && !video.job) {
      if (now.saved.job.job_type === 'video_images') setPicturesDrawn((n) => n + 1)
      const done = DONE_TEXT[now.saved.job.job_type]
      if (done && !video.error) setNotice(done)
    }
    const untouched = editsKey(now.scenes) === now.base.edits
    const fresh = toDrafts(video, nextKey)
    if (untouched && editsKey(fresh) !== now.base.edits) {
      adopt(video)
      return
    }
    setSaved(video)
    if (untouched) setBase((b) => ({ ...b, version: video.draft_version }))
  }, [adopt, nextKey])

  // The first load, and the retry after a failed one. A result that arrives after the page has
  // gone is dropped, and state is set only from promise callbacks.
  useEffect(() => {
    let cancelled = false
    if (!id) return
    getVideo(id).then(
      (video) => {
        if (cancelled) return
        adopt(video)
        setLoading(false)
      },
      (err) => {
        if (cancelled) return
        // Only a 404 means the video is gone; anything else is a failed load that a retry may fix.
        if (axios.isAxiosError(err) && err.response?.status === 404) setMissing(true)
        else setLoadFailed(true)
        setLoading(false)
      },
    )
    return () => {
      cancelled = true
    }
  }, [id, attempt, adopt])

  // A job runs in the worker, so the server's detail is the truth until it ends.
  const runningJob: VideoJob['job_type'] | null = saved ? (saved.job?.job_type ?? STATUS_JOB[saved.status] ?? null) : null
  useEffect(() => {
    if (!runningJob || !id) return
    const timer = setInterval(async () => {
      try {
        follow(await getVideo(id))
      } catch {
        // transient failure: the next tick retries
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [runningJob, id, follow])

  const dirty = editsKey(scenes) !== base.edits
  const unsaved = saved !== null && saved.status !== 'generating' && dirty
  useLeaveGuard(unsaved)

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  if (loading) {
    return <AppShell title="Video"><p className="course-empty">Đang tải...</p></AppShell>
  }
  if (missing) {
    return (
      <AppShell title="Video">
        <p className="text-sm danger-text">Không tìm thấy video.</p>
        <Link to="/courses" className="link-action">Về danh sách môn học</Link>
      </AppShell>
    )
  }
  if (loadFailed || !saved || !id) {
    return (
      <AppShell title="Video">
        <p role="alert" className="text-sm danger-text">Không tải được video.</p>
        <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
      </AppShell>
    )
  }

  const videoId = id
  const courseId = saved.course_id
  const titleProps = editorTitle('Video', saved.focus)
  const published = saved.published_at !== null
  const rendered = saved.rendered_version
  const jobRunning = runningJob !== null
  const seconds = estimatedSeconds(scenes)
  const overLong = seconds > saved.max_seconds
  const missingTitle = scenes.some((s) => s.title.trim() === '')
  const imageModel = IMAGE_MODEL_CHOICES.find((c) => c.alias === saved.image_alias)
  const voice = VOICE_CHOICES.find((c) => c.alias === saved.voice_alias)
  const toDraw = saved.scenes.filter((s) => s.needs_image || s.image_outdated).length
  // The saved scenes as edits, to tell which scene's slide no longer shows what is being edited.
  const savedEdits = toEdits(toDrafts(saved, () => 0))
  const spent = Object.values(saved.cost ?? {}).reduce((sum, part) => sum + (part?.usd ?? 0), 0)

  const backLink = (
    <Link to={`/courses/${courseId}`} onClick={(e) => { if (!confirmLeave(unsaved)) e.preventDefault() }} className="link-action">
      Quay lại
    </Link>
  )

  async function remove() {
    if (!window.confirm('Xóa vĩnh viễn video này, cả slide, hình và giọng đã tạo? Không thể khôi phục.')) return
    setBusy('delete')
    setError(null)
    try {
      await deleteVideo(videoId)
      navigate(`/courses/${courseId}`)
    } catch (err) {
      setError(apiErrorMessage(err, 'Xóa video thất bại'))
      setBusy(null)
    }
  }

  const deleteButton = (
    <button type="button" onClick={remove} disabled={busy !== null || jobRunning} className="link-action danger">
      {busy === 'delete' ? 'Đang xóa...' : 'Xóa video'}
    </button>
  )

  if (saved.status === 'generating') {
    return (
      <AppShell {...titleProps}>
        {backLink}
        <p role="status" className="course-empty">Đang sinh lời thoại...</p>
      </AppShell>
    )
  }

  // Only a failed script leaves nothing to edit; a failed picture or render keeps the scenes.
  if (saved.status === 'failed' && saved.scenes.length === 0) {
    return (
      <AppShell {...titleProps}>
        <p className="text-sm danger-text">{saved.error ?? 'Sinh lời thoại thất bại.'} Xóa video này rồi sinh lại từ trang môn học.</p>
        {error && <p role="alert" className="mt-3 text-sm danger-text">{error}</p>}
        <div className="mt-4 flex gap-4">
          {backLink}
          {deleteButton}
        </div>
      </AppShell>
    )
  }

  function editScene(key: number, change: Partial<SceneDraft>) {
    setNotice(null)
    setScenes((list) => list.map((s) => (s.key === key ? { ...s, ...change } : s)))
  }

  function editItems(key: number, field: 'bullets' | 'sentences', change: (items: ItemDraft[]) => ItemDraft[]) {
    setNotice(null)
    setScenes((list) => list.map((s) => (s.key === key ? { ...s, [field]: change(s[field]) } : s)))
  }

  function moveScene(index: number, by: number) {
    setNotice(null)
    setScenes((list) => {
      const next = [...list]
      const [moved] = next.splice(index, 1)
      next.splice(index + by, 0, moved)
      return next
    })
  }

  async function run(kind: 'draw' | 'render' | 'publish' | 'reload', action: () => Promise<VideoDetail>, fallback: string) {
    setBusy(kind)
    setError(null)
    setNotice(null)
    try {
      const video = await action()
      if (kind === 'reload') adopt(video)
      else follow(video)
      if (kind === 'reload') setStale(false)
    } catch (err) {
      // A quota (429) or a job in the way (409) comes with the server's own Vietnamese wording.
      setError(apiErrorMessage(err, fallback))
    } finally {
      setBusy(null)
    }
  }

  async function save() {
    setBusy('save')
    setError(null)
    setNotice(null)
    try {
      adopt(await saveVideoScenes(videoId, base.version, toEdits(scenes)))
      setStale(false)
      setNotice('Đã lưu.')
    } catch (err) {
      // A 409 here is a newer draft saved elsewhere: saving again would be refused the same way.
      setStale(axios.isAxiosError(err) && err.response?.status === 409)
      setError(apiErrorMessage(err, 'Lưu thay đổi thất bại'))
    } finally {
      setBusy(null)
    }
  }

  function reload() {
    if (!confirmLeave(dirty)) return
    void run('reload', () => getVideo(videoId), 'Không tải lại được bản nháp')
  }

  function draw(sceneIndexes?: number[]) {
    void run('draw', async () => {
      await drawIllustrations(videoId, sceneIndexes)
      return getVideo(videoId)
    }, 'Vẽ hình thất bại')
  }

  function startRender() {
    if (saved && saved.render_warnings.length > 0
      && !window.confirm(`${saved.render_warnings.join('\n')}\n\nVẫn dựng video?`)) return
    void run('render', async () => {
      await renderVideo(videoId)
      return getVideo(videoId)
    }, 'Dựng video thất bại')
  }

  const needsSave = dirty ? 'Lưu thay đổi trước: hình và video được làm từ bản đã lưu.' : null

  return (
    <AppShell {...titleProps}>
      <div className="video-editor">
        <div className="flashcard-bar">
          {backLink}
          <span className="status-pill" data-status={published ? 'embedded' : undefined}>
            {published ? 'Đã giao cho sinh viên' : 'Bản nháp'}
          </span>
          {runningJob && <span role="status" className="status-pill" data-status="processing">{JOB_TEXT[runningJob]}</span>}
          <span className="flashcard-count">{scenes.length}/{MAX_SCENES} cảnh</span>
          <span className={`flashcard-count${overLong ? ' danger-text' : ''}`}>
            Độ dài ước tính: {formatMinutes(seconds)} phút (tối đa {formatMinutes(saved.max_seconds)} phút)
          </span>
          {spent > 0 && <span className="flashcard-count">Đã tốn khoảng {formatUsd(spent)}</span>}
        </div>
        <p className="course-hint">
          Sửa lời thoại và chữ trên slide, lưu, rồi vẽ hình, dựng video và giao cho sinh viên.
        </p>
        {saved.error && <p className="text-sm danger-text">{saved.error}</p>}

        <ol className="video-scenes" aria-label="Các cảnh">
          {scenes.map((s, index) => {
            const number = index + 1
            const origin = s.fromIndex === null ? undefined : saved.scenes[s.fromIndex]
            const uncited = [...filled(s.bullets), ...filled(s.sentences)].some((item) => item.citations.length === 0)
            const wholeLabel = WHOLE_ITEM_LABEL[s.kind]
            // A flag about an item that was removed, or about a field the scene's kind no longer
            // has, is dropped by the save, so it is neither shown nor counted.
            const shownLabels = [...s.bullets, ...s.sentences].map((item) => item.label).concat(wholeLabel ?? null)
            const flagged = uncited || sceneFlags(s.flags).length > 0 || shownLabels.some((label) => flagsFor(s.flags, label).length > 0)
            const changed = s.fromIndex !== null && JSON.stringify(toEdits([s])[0]) !== JSON.stringify(savedEdits[s.fromIndex])
            const drawable = origin !== undefined && origin.kind === 'illustration' && !!origin.image_prompt?.trim()
            const dismiss = (flag: string) => editScene(s.key, { flags: s.flags.filter((f) => f !== flag) })
            const itemProps = (field: 'bullets' | 'sentences', item: ItemDraft) => ({
              item,
              sources: s.sources,
              flags: flagsFor(s.flags, item.label),
              onText: (text: string) => editItems(s.key, field, (items) => items.map((x) => (x.key === item.key ? { ...x, text } : x))),
              onCitations: (citations: string[]) =>
                editItems(s.key, field, (items) => items.map((x) => (x.key === item.key ? { ...x, citations } : x))),
              onDismiss: dismiss,
              onRemove: () => editItems(s.key, field, (items) => items.filter((x) => x.key !== item.key)),
            })
            return (
              <li key={s.key} aria-label={`Cảnh ${number}`} className="flashcard-item video-scene">
                <div className="flashcard-head">
                  <div className="video-scene-title">
                    <span className="part-label">Cảnh {number}</span>
                    {flagged && <span className="video-flag-badge">Cần xem lại</span>}
                  </div>
                  <div className="video-scene-actions">
                    <button type="button" onClick={() => moveScene(index, -1)} disabled={index === 0}
                      aria-label={`Đưa cảnh ${number} lên`} className="link-action small-action">Lên</button>
                    <button type="button" onClick={() => moveScene(index, 1)} disabled={index === scenes.length - 1}
                      aria-label={`Đưa cảnh ${number} xuống`} className="link-action small-action">Xuống</button>
                    <button type="button" onClick={() => { setNotice(null); setScenes((list) => list.filter((x) => x.key !== s.key)) }}
                      disabled={scenes.length === 1} aria-label={`Xóa cảnh ${number}`} className="link-action danger small-action">
                      Xóa cảnh
                    </button>
                  </div>
                </div>
                {sceneFlags(s.flags).map((flag) => (
                  <FlagNote key={flag} flag={flag} dismissLabel={`Bỏ đánh dấu cảnh ${number}`} onDismiss={() => dismiss(flag)} />
                ))}

                {origin && s.fromIndex !== null ? (
                  <ScenePreview
                    key={s.fromIndex}
                    videoId={videoId}
                    index={s.fromIndex}
                    version={slideVersion(origin, s.fromIndex, picturesDrawn)}
                    number={number}
                    limiter={limiter}
                    note={changed ? 'Slide của bản đã lưu; lưu để xem bản đang sửa.' : undefined}
                  />
                ) : (
                  <SlidePlaceholder text="Lưu để xem slide của cảnh mới." />
                )}
                {drawable && origin && (
                  <div className="video-picture-row">
                    <span className="course-hint">
                      {origin.image_outdated ? 'Hình được vẽ theo mô tả cũ.'
                        : origin.has_image ? 'Đã có hình minh họa.' : 'Chưa vẽ hình minh họa.'}
                    </span>
                    <button
                      type="button"
                      onClick={() => draw([s.fromIndex as number])}
                      disabled={dirty || jobRunning || busy !== null}
                      aria-label={`${origin.has_image ? 'Vẽ lại' : 'Vẽ'} hình cảnh ${number}`}
                      className="link-action small-action"
                    >
                      {origin.has_image ? 'Vẽ lại hình' : 'Vẽ hình'}
                    </button>
                  </div>
                )}

                <div className="video-scene-fields">
                  <label className="flashcard-field">
                    Loại cảnh
                    <select aria-label={`Loại cảnh ${number}`} value={s.kind}
                      onChange={(e) => editScene(s.key, { kind: e.target.value as SceneKind })} className="ui-select">
                      {(Object.keys(KIND_LABEL) as SceneKind[]).map((kind) => (
                        <option key={kind} value={kind}>{KIND_LABEL[kind]}</option>
                      ))}
                    </select>
                  </label>
                  <label className="flashcard-field">
                    Tiêu đề
                    <input aria-label={`Tiêu đề cảnh ${number}`} value={s.title} maxLength={300}
                      onChange={(e) => editScene(s.key, { title: e.target.value })} className="ui-input w-full" />
                  </label>
                </div>

                <div className="flashcard-field">
                  Ý chính trên slide
                  <ul className="video-items" aria-label={`Ý chính cảnh ${number}`}>
                    {s.bullets.map((item, i) => (
                      <ItemEditor key={item.key} name={`ý ${i + 1} cảnh ${number}`} rows={2} maxLength={500} {...itemProps('bullets', item)} />
                    ))}
                  </ul>
                  <button type="button" disabled={s.bullets.length >= MAX_BULLETS}
                    onClick={() => editItems(s.key, 'bullets', (items) => [...items, { key: nextKey(), text: '', citations: [], label: null }])}
                    className="link-action small-action self-start">
                    Thêm ý
                  </button>
                </div>

                {s.kind === 'formula' && (
                  <label className="flashcard-field">
                    Công thức (LaTeX)
                    <textarea aria-label={`Công thức cảnh ${number}`} value={s.formula} maxLength={2000} rows={2}
                      onChange={(e) => editScene(s.key, { formula: e.target.value })} className="ui-input w-full font-mono" />
                  </label>
                )}
                {s.kind === 'code' && (
                  <label className="flashcard-field">
                    Code
                    <textarea aria-label={`Code cảnh ${number}`} value={s.code} maxLength={4000} rows={6} spellCheck={false}
                      onChange={(e) => editScene(s.key, { code: e.target.value })} className="ui-input w-full font-mono" />
                  </label>
                )}
                {s.kind === 'table' && (
                  <label className="flashcard-field">
                    Bảng (mỗi dòng một hàng, các ô cách nhau bằng dấu |)
                    <textarea aria-label={`Bảng cảnh ${number}`} value={s.table} rows={4}
                      onChange={(e) => editScene(s.key, { table: e.target.value })} className="ui-input w-full font-mono" />
                  </label>
                )}
                {wholeLabel && flagsFor(s.flags, wholeLabel).map((flag) => (
                  <FlagNote key={flag} flag={flag} dismissLabel={`Bỏ đánh dấu ${wholeLabel.toLowerCase()} cảnh ${number}`}
                    onDismiss={() => dismiss(flag)} />
                ))}
                {s.kind === 'illustration' && (
                  <label className="flashcard-field">
                    Mô tả hình minh họa
                    <textarea aria-label={`Mô tả hình cảnh ${number}`} value={s.imagePrompt} maxLength={2000} rows={3}
                      onChange={(e) => editScene(s.key, { imagePrompt: e.target.value })} className="ui-input w-full" />
                    <span className="course-hint">
                      Ghi đủ số liệu và nhãn có trong hình. Để trống thì cảnh chỉ có chữ.
                    </span>
                  </label>
                )}

                <div className="flashcard-field">
                  Lời thoại (mỗi ô một câu)
                  <ul className="video-items" aria-label={`Lời thoại cảnh ${number}`}>
                    {s.sentences.map((item, i) => (
                      <ItemEditor key={item.key} name={`câu ${i + 1} cảnh ${number}`} rows={2} maxLength={6000} {...itemProps('sentences', item)} />
                    ))}
                  </ul>
                  <button type="button"
                    onClick={() => editItems(s.key, 'sentences', (items) => [...items, { key: nextKey(), text: '', citations: [], label: null }])}
                    className="link-action small-action self-start">
                    Thêm câu
                  </button>
                </div>

                <details className="video-scene-sources">
                  <summary>Đoạn nguồn của cảnh ({s.sources.length})</summary>
                  <SourcePassages sources={s.sources} label={`Nguồn của cảnh ${number}`} />
                </details>
              </li>
            )
          })}
        </ol>

        <button
          type="button"
          onClick={() => {
            setNotice(null)
            setScenes((list) => [...list, {
              key: nextKey(), fromIndex: null, kind: 'summary', title: '', bullets: [],
              sentences: [{ key: nextKey(), text: '', citations: [], label: null }],
              formula: '', code: '', table: '', imagePrompt: '', flags: [], sources: [],
            }])
          }}
          disabled={scenes.length >= MAX_SCENES}
          className="ui-button"
        >
          Thêm cảnh
        </button>

        <div className="video-save">
          {missingTitle && <p className="text-sm danger-text">Mỗi cảnh cần một tiêu đề.</p>}
          {overLong && (
            <p className="text-sm danger-text">Quá {formatMinutes(saved.max_seconds)} phút: hãy rút ngắn lời thoại trước khi dựng.</p>
          )}
          {notice && <p role="status" className="text-sm success-text">{notice}</p>}
          {error && <p role="alert" className="text-sm danger-text">{error}</p>}
          <div className="flashcard-actions">
            <button type="button" onClick={save} disabled={!dirty || missingTitle || busy !== null} className="ui-button primary">
              {busy === 'save' ? 'Đang lưu...' : 'Lưu thay đổi'}
            </button>
            {stale && (
              <button type="button" onClick={reload} disabled={busy !== null} className="ui-button">
                {busy === 'reload' ? 'Đang tải...' : 'Tải lại bản nháp'}
              </button>
            )}
          </div>
        </div>

        <section className="video-step" aria-labelledby="video-draw-title">
          <h2 id="video-draw-title" className="course-section-title">Vẽ hình</h2>
          <p className="course-hint">
            Chỉ cảnh minh họa có mô tả mới được vẽ, tối đa 5 cảnh.
            {imageModel && ` Mỗi hình ${imageModel.price} (${imageModel.label}).`}
          </p>
          {toDraw === 0 && <p className="course-hint">Mọi hình đã khớp với mô tả.</p>}
          <button type="button" onClick={() => draw()} disabled={dirty || jobRunning || busy !== null || toDraw === 0} className="ui-button">
            {busy === 'draw' ? 'Đang gửi...' : 'Vẽ hình'}
          </button>
        </section>

        <section className="video-step" aria-labelledby="video-render-title">
          <h2 id="video-render-title" className="course-section-title">Dựng video</h2>
          {voice && <p className="course-hint">Giọng {voice.label}, {voice.price}. Cảnh không đổi dùng lại giọng đã tạo.</p>}
          {saved.render_problems.length > 0 && (
            <ul className="video-problems" aria-label="Chưa dựng được vì">
              {saved.render_problems.map((problem) => <li key={problem} className="text-sm danger-text">{problem}</li>)}
            </ul>
          )}
          {saved.render_warnings.map((warning) => <p key={warning} className="doc-warning">{warning}</p>)}
          {needsSave && <p className="course-hint">{needsSave}</p>}
          <button
            type="button"
            onClick={startRender}
            disabled={dirty || jobRunning || busy !== null || saved.render_problems.length > 0}
            className="ui-button primary"
          >
            {busy === 'render' ? 'Đang gửi...' : rendered === null ? 'Dựng video' : 'Dựng lại video'}
          </button>
        </section>

        <section className="video-step" aria-labelledby="video-result-title">
          <h2 id="video-result-title" className="course-section-title">Xem video</h2>
          {rendered === null ? (
            <p className="course-hint">Chưa có video đã dựng.</p>
          ) : (
            <>
              {(saved.draft_version > rendered || dirty) && (
                <p className="doc-warning">
                  Bản nháp đã sửa sau lần dựng gần nhất: video dưới đây là bản cũ
                  {published ? ', và sinh viên vẫn xem bản này' : ''}. Dựng lại để cập nhật.
                </p>
              )}
              <RenderedVideo videoId={videoId} renderedVersion={rendered} />
              {saved.duration_seconds !== null && (
                <p className="course-hint">Dài {formatMinutes(saved.duration_seconds)} phút.</p>
              )}
            </>
          )}
        </section>

        <section className="video-step" aria-labelledby="video-publish-title">
          <h2 id="video-publish-title" className="course-section-title">Giao cho sinh viên</h2>
          {rendered === null && <p className="course-hint">Dựng video trước khi giao.</p>}
          <div className="flashcard-actions">
            <button
              type="button"
              onClick={() => void run('publish', () => publishVideo(videoId, !published), published ? 'Ngừng giao thất bại' : 'Giao video thất bại')}
              disabled={busy !== null || (!published && rendered === null)}
              className="ui-button"
            >
              {busy === 'publish' ? 'Đang xử lý...' : published ? 'Ngừng giao' : 'Giao cho sinh viên'}
            </button>
            {deleteButton}
          </div>
        </section>
      </div>
    </AppShell>
  )
}
