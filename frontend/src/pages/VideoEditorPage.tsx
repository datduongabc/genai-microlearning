import axios from 'axios'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  apiErrorMessage,
  deleteVideo,
  drawIllustrations,
  getVideo,
  MAX_SCENES,
  publishVideo,
  renderVideo,
  saveVideoScenes,
  type VideoDetail,
  type VideoJob,
} from '../api'
import { AppShell } from '../components/AppShell'
import { RenderedVideo } from '../components/RenderedVideo'
import { VideoSceneEditor } from '../components/VideoSceneEditor'
import { formatMinutes } from '../lib/duration'
import { editorTitle } from '../lib/editorTitle'
import { createLimiter } from '../lib/limiter'
import { useMediaModels } from '../lib/useMediaModels'
import { confirmLeave, useLeaveGuard } from '../lib/useLeaveGuard'
import { editsKey, estimatedSeconds, newScene, toDrafts, toEdits, type SceneDraft } from '../lib/videoDraft'

const POLL_INTERVAL_MS = 3000
// At most two slides are asked for at once: the server renders two at a time per process.
const PREVIEWS_AT_ONCE = 2

type JobType = VideoJob['job_type']

const JOB_TEXT: Record<JobType, string> = {
  video_script: 'Đang sinh lời thoại...',
  video_images: 'Đang vẽ hình...',
  video_render: 'Đang dựng video...',
}
// A job between being picked up and the next poll shows only in the status.
const STATUS_JOB: Partial<Record<VideoDetail['status'], JobType>> = {
  generating: 'video_script',
  drawing: 'video_images',
  rendering: 'video_render',
}
const DONE_TEXT: Partial<Record<JobType, string>> = {
  video_images: 'Đã vẽ xong hình.',
  video_render: 'Đã dựng xong video.',
}
const QUEUED_TEXT: Partial<Record<JobType, string>> = {
  video_images: 'Đã gửi yêu cầu vẽ hình; trang sẽ tự cập nhật khi xong.',
  video_render: 'Đã gửi yêu cầu dựng video; trang sẽ tự cập nhật khi xong.',
}

// The catalogue words a price as "khoảng $0,037 một ảnh" for the model picker; beside "Mỗi hình" the
// unit would be said twice.
function perImage(price: string): string {
  return price.replace(/ một ảnh$/, '')
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString('vi-VN', { maximumFractionDigits: 3 })}`
}

interface Base {
  // The draft_version the edits are made on, sent with the save.
  version: number
  // The edits of that draft, to tell what is unsaved.
  edits: string
}

export function VideoEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [saved, setSaved] = useState<VideoDetail | null>(null)
  const [scenes, setScenes] = useState<SceneDraft[]>([])
  const [base, setBase] = useState<Base>({ version: 0, edits: '[]' })
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const [missing, setMissing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState<'save' | 'draw' | 'render' | 'publish' | 'delete' | 'reload' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  // A notice is shown beside the control that caused it: a finished job in its own section, the
  // rest (saved, edited) above the save button.
  const [shownNotice, setShownNotice] = useState<{ text: string; area: 'save' | JobType } | null>(null)
  const setNotice = useCallback((text: string | null, area: 'save' | JobType = 'save') => {
    setShownNotice(text === null ? null : { text, area })
  }, [])
  const noticeIn = (area: 'save' | JobType) => (shownNotice?.area === area ? shownNotice.text : null)
  // Goes up when a drawing job ends, so an illustration's slide is asked for again even when a
  // redraw changed nothing but the picture.
  const [picturesDrawn, setPicturesDrawn] = useState(0)
  // A job this page asked for and has not yet seen running or ended: it is followed even when the
  // detail could not be read right after asking, or the job was over by the first read.
  const [awaitingJob, setAwaitingJob] = useState<JobType | null>(null)
  const keyCounter = useRef(0)
  const nextKey = useCallback(() => keyCounter.current++, [])
  const limiter = useMemo(() => createLimiter(PREVIEWS_AT_ONCE), [])
  const models = useMediaModels()

  // What a poll compares with; a ref, because the poll's callback outlives the render it was made in.
  const latest = useRef({ saved, scenes, base, awaitingJob })
  useEffect(() => {
    latest.current = { saved, scenes, base, awaitingJob }
  })

  const awaitJob = useCallback((job: JobType | null) => {
    latest.current.awaitingJob = job
    setAwaitingJob(job)
  }, [])

  // Takes a fresh draft as the one being edited, dropping any edit. A scene keeps the key of the
  // one shown in its place, so a save does not mount its slide again and fetch it for nothing.
  const adopt = useCallback((video: VideoDetail) => {
    const shown = latest.current.scenes
    const drafts = toDrafts(video, nextKey).map((d, i) => (i < shown.length ? { ...d, key: shown[i].key } : d))
    const next = { version: video.draft_version, edits: editsKey(drafts) }
    // Set at once too, so an answer that arrives before the next render compares with this draft.
    latest.current = { ...latest.current, saved: video, scenes: drafts, base: next }
    setSaved(video)
    setScenes(drafts)
    setBase(next)
  }, [nextKey])

  // A detail from a poll or an action. The lecturer's unsaved edits are never replaced by it, and a
  // draft nobody touched follows the server.
  const follow = useCallback((video: VideoDetail) => {
    const now = latest.current
    // An answer older than the draft in hand (a poll that left before a save) is dropped whole:
    // its job, status and rendered video are older than what the save answered with.
    if (video.draft_version < now.base.version) return
    const ended = now.saved?.job?.job_type ?? now.awaitingJob ?? (now.saved ? STATUS_JOB[now.saved.status] : undefined)
    if (video.job) {
      if (now.awaitingJob) awaitJob(null)
    } else if (ended) {
      if (ended === 'video_images') setPicturesDrawn((n) => n + 1)
      const done = DONE_TEXT[ended]
      if (done && !video.error) setNotice(done, ended)
      awaitJob(null)
    }
    const untouched = editsKey(now.scenes) === now.base.edits
    if (untouched && editsKey(toDrafts(video, nextKey)) !== now.base.edits) {
      adopt(video)
      return
    }
    latest.current = { ...latest.current, saved: video }
    setSaved(video)
    if (untouched) setBase((b) => ({ ...b, version: video.draft_version }))
  }, [adopt, awaitJob, nextKey, setNotice])

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

  // A job runs in the worker, so the server's detail is the truth until it ends. One read at a
  // time: the next is planned only once the last has answered, and never while one is still out
  // from an earlier round of polling.
  const runningJob: JobType | null = saved ? (saved.job?.job_type ?? STATUS_JOB[saved.status] ?? awaitingJob) : null
  const polling = useRef(false)
  useEffect(() => {
    if (!runningJob || !id) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      if (!polling.current) {
        polling.current = true
        try {
          const video = await getVideo(id)
          if (!cancelled) follow(video)
        } catch {
          // transient failure: the next tick retries
        } finally {
          polling.current = false
        }
      }
      if (!cancelled) timer = setTimeout(() => void tick(), POLL_INTERVAL_MS)
    }
    timer = setTimeout(() => void tick(), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
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
  const choices = models && models !== 'failed' ? models : null
  const imageModel = choices?.images.find((c) => c.alias === saved.image_alias)
  const voice = choices?.voices.find((c) => c.alias === saved.voice_alias)
  const toDraw = saved.scenes.filter((s) => s.needs_image || s.image_outdated).length
  // The saved scenes as edits, to tell which scene's slide no longer shows what is being edited.
  const savedEdits = toEdits(toDrafts(saved, () => 0)).map((e) => JSON.stringify(e))
  const spent = Object.values(saved.cost ?? {}).reduce((sum, part) => sum + (part?.usd ?? 0), 0)
  const canStartJob = !dirty && !jobRunning && busy === null

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

  function editScene(key: number, change: (scene: SceneDraft) => SceneDraft) {
    setNotice(null)
    setScenes((list) => list.map((s) => (s.key === key ? change(s) : s)))
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

  async function act(kind: 'publish' | 'reload', action: () => Promise<VideoDetail>, fallback: string) {
    setBusy(kind)
    setError(null)
    setNotice(null)
    try {
      const video = await action()
      if (kind === 'reload') {
        adopt(video)
        setStale(false)
      } else {
        follow(video)
      }
    } catch (err) {
      setError(apiErrorMessage(err, fallback))
    } finally {
      setBusy(null)
    }
  }

  // Asks for a picture or a render, then reads the detail to follow the job.
  async function startJob(kind: 'draw' | 'render', job: JobType, ask: () => Promise<unknown>, fallback: string) {
    setBusy(kind)
    setError(null)
    setNotice(null)
    try {
      await ask()
    } catch (err) {
      // A quota (429) or a job in the way (409) comes with the server's own Vietnamese wording.
      setError(apiErrorMessage(err, fallback))
      setBusy(null)
      return
    }
    // The job is queued whatever happens next, so it is followed even if this read fails.
    awaitJob(job)
    try {
      follow(await getVideo(videoId))
    } catch {
      setNotice(QUEUED_TEXT[job] ?? null, job)
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
    void act('reload', () => getVideo(videoId), 'Không tải lại được bản nháp')
  }

  function draw(sceneIndexes?: number[]) {
    void startJob('draw', 'video_images', () => drawIllustrations(videoId, sceneIndexes), 'Vẽ hình thất bại')
  }

  function startRender() {
    if (saved && saved.render_warnings.length > 0
      && !window.confirm(`${saved.render_warnings.join('\n')}\n\nVẫn dựng video?`)) return
    void startJob('render', 'video_render', () => renderVideo(videoId), 'Dựng video thất bại')
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
          {scenes.map((s, index) => (
            <VideoSceneEditor
              key={s.key}
              scene={s}
              number={index + 1}
              count={scenes.length}
              documentId={saved.document_id}
              slide={{
                videoId,
                limiter,
                picturesDrawn,
                origin: s.fromIndex === null ? undefined : saved.scenes[s.fromIndex],
                changed: s.fromIndex !== null && JSON.stringify(toEdits([s])[0]) !== savedEdits[s.fromIndex],
              }}
              canDraw={canStartJob}
              nextKey={nextKey}
              onChange={(change) => editScene(s.key, change)}
              onMove={(by) => moveScene(index, by)}
              onRemove={() => { setNotice(null); setScenes((list) => list.filter((x) => x.key !== s.key)) }}
              onDraw={(sceneIndex) => draw([sceneIndex])}
            />
          ))}
        </ol>

        <button
          type="button"
          onClick={() => { setNotice(null); setScenes((list) => [...list, newScene(nextKey)]) }}
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
          {noticeIn('save') && <p role="status" className="text-sm success-text">{noticeIn('save')}</p>}
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
            {imageModel && ` Mỗi hình ${perImage(imageModel.price)} (${imageModel.label}).`}
          </p>
          {toDraw === 0 && <p className="course-hint">Mọi hình đã khớp với mô tả.</p>}
          <button type="button" onClick={() => draw()} disabled={!canStartJob || toDraw === 0} className="ui-button">
            {busy === 'draw' ? 'Đang gửi...' : 'Vẽ hình'}
          </button>
          {noticeIn('video_images') && <p role="status" className="text-sm success-text">{noticeIn('video_images')}</p>}
        </section>

        <section className="video-step" aria-labelledby="video-render-title">
          <h2 id="video-render-title" className="course-section-title">Dựng video</h2>
          {voice && <p className="course-hint">Giọng {voice.label}, {voice.price}. Cảnh không đổi dùng lại giọng đã tạo.</p>}
          {saved.render_problems.length > 0 && (
            <ul className="video-problems" aria-label="Chưa dựng được vì">
              {saved.render_problems.map((problem, i) => <li key={`${i}-${problem}`} className="text-sm danger-text">{problem}</li>)}
            </ul>
          )}
          {saved.render_warnings.map((warning, i) => <p key={`${i}-${warning}`} className="doc-warning">{warning}</p>)}
          {needsSave && <p className="course-hint">{needsSave}</p>}
          <button
            type="button"
            onClick={startRender}
            disabled={!canStartJob || saved.render_problems.length > 0}
            className="ui-button primary"
          >
            {busy === 'render' ? 'Đang gửi...' : rendered === null ? 'Dựng video' : 'Dựng lại video'}
          </button>
          {noticeIn('video_render') && <p role="status" className="text-sm success-text">{noticeIn('video_render')}</p>}
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
              onClick={() => void act('publish', () => publishVideo(videoId, !published), published ? 'Ngừng giao thất bại' : 'Giao video thất bại')}
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
