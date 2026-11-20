import { confirmAction } from '../lib/confirm'
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
import { notifyDone, notifyFailed } from '../lib/notify'
import { editorTitle } from '../lib/editorTitle'
import { createLimiter } from '../lib/limiter'
import { confirmLeave, useLeaveGuard } from '../lib/useLeaveGuard'
import { editsKey, estimatedSeconds, newScene, toDrafts, toEdits, type SceneDraft } from '../lib/videoDraft'

// One toast for a job's failure, so a poll and the first load do not show it twice.
const VIDEO_ERROR_TOAST = 'video-error'
const POLL_INTERVAL_MS = 3000
// At most two slides are asked for at once: the server renders two at a time per process.
const PREVIEWS_AT_ONCE = 2

type JobType = VideoJob['job_type']

const JOB_TEXT: Record<JobType, string> = {
  video_script: 'Đang tạo…',
  video_images: 'Đang vẽ hình…',
  video_render: 'Đang dựng video…',
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
  video_images: 'Đã gửi yêu cầu vẽ hình. Trang tự cập nhật khi xong.',
  video_render: 'Đã gửi yêu cầu dựng video. Trang tự cập nhật khi xong.',
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
  const [stale, setStale] = useState(false)
  // The one scene open for editing, by its draft key; the others are read as the learner sees them.
  const [openKey, setOpenKey] = useState<number | null>(null)
  // Goes up when a drawing job ends, so an illustration's slide is asked for again even when a
  // redraw changed nothing but the picture.
  const [picturesDrawn, setPicturesDrawn] = useState(0)
  // A job this page asked for and has not yet seen running or ended: it is followed even when the
  // detail could not be read right after asking, or the job was over by the first read.
  const [awaitingJob, setAwaitingJob] = useState<JobType | null>(null)
  const keyCounter = useRef(0)
  const nextKey = useCallback(() => keyCounter.current++, [])
  const limiter = useMemo(() => createLimiter(PREVIEWS_AT_ONCE), [])

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
      // A job can end while the lecturer is further down the page, so its result is a toast.
      if (video.error) notifyFailed(video.error, VIDEO_ERROR_TOAST)
      else if (done) notifyDone(done)
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
  }, [adopt, awaitJob, nextKey])

  // The first load, and the retry after a failed one. A result that arrives after the page has
  // gone is dropped, and state is set only from promise callbacks.
  useEffect(() => {
    let cancelled = false
    if (!id) return
    getVideo(id).then(
      (video) => {
        if (cancelled) return
        adopt(video)
        // What went wrong in a job that ended while the page was closed, e.g. a picture not drawn.
        if (video.error && video.scenes.length > 0) notifyFailed(video.error, VIDEO_ERROR_TOAST)
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
  const allowLeave = useLeaveGuard(unsaved)

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  if (loading) {
    return <AppShell title="Video"><p className="course-empty">Đang tải…</p></AppShell>
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
  const toDraw = saved.scenes.filter((s) => s.needs_image || s.image_outdated).length
  // The saved scenes as edits, to tell which scene's slide no longer shows what is being edited.
  const savedEdits = toEdits(toDrafts(saved, () => 0)).map((e) => JSON.stringify(e))
  const canStartJob = !dirty && !jobRunning && busy === null

  const backLink = (
    <Link to={`/courses/${courseId}`} className="link-action">
      Về trang môn học
    </Link>
  )

  async function remove() {
    if (!await confirmAction({ title: 'Xóa video này?', description: 'Slide, hình và giọng đã tạo bị xóa theo. Không thể khôi phục.', action: 'Xóa video', destructive: true })) return
    setBusy('delete')
    try {
      await deleteVideo(videoId)
      // What was being edited is gone, so its unsaved edits are not worth a question.
      allowLeave()
      notifyDone('Đã xóa video.')
      navigate(`/courses/${courseId}`)
    } catch (err) {
      notifyFailed(apiErrorMessage(err, 'Xóa video thất bại'))
      setBusy(null)
    }
  }

  const deleteButton = (
    <button type="button" onClick={remove} disabled={busy !== null || jobRunning} className="ui-button danger ml-auto">
      {busy === 'delete' ? 'Đang xóa…' : 'Xóa video'}
    </button>
  )

  if (saved.status === 'generating') {
    return (
      <AppShell {...titleProps}>
        {backLink}
        <p role="status" className="course-empty">Đang tạo…</p>
      </AppShell>
    )
  }

  // Only a failed script leaves nothing to edit; a failed picture or render keeps the scenes.
  if (saved.status === 'failed' && saved.scenes.length === 0) {
    return (
      <AppShell {...titleProps}>
        <p className="text-sm danger-text">{saved.error ?? 'Viết lời thoại thất bại.'} Xóa video này rồi tạo lại từ trang môn học.</p>
        <div className="mt-4 flex gap-4">
          {backLink}
          {deleteButton}
        </div>
      </AppShell>
    )
  }

  function editScene(key: number, change: (scene: SceneDraft) => SceneDraft) {
    setScenes((list) => list.map((s) => (s.key === key ? change(s) : s)))
  }

  function moveScene(index: number, by: number) {
    const next = [...scenes]
    const [moved] = next.splice(index, 1)
    next.splice(index + by, 0, moved)
    void save(next, 'Đã đổi thứ tự cảnh.')
  }

  async function removeScene(index: number) {
    if (!await confirmAction({ title: `Xóa cảnh ${index + 1}?`, description: 'Không thể khôi phục.', action: 'Xóa cảnh', destructive: true })) return
    void save(scenes.filter((_, i) => i !== index), 'Đã xóa cảnh.')
  }

  function addScene() {
    const scene = newScene(nextKey)
    setScenes((list) => [...list, scene])
    setOpenKey(scene.key)
  }

  // "Hủy": the scenes as saved, which drops the open scene's edits or the scene being added.
  function cancel() {
    if (saved) adopt(saved)
    setOpenKey(null)
  }

  async function act(kind: 'publish' | 'reload', action: () => Promise<VideoDetail>, fallback: string) {
    setBusy(kind)
    try {
      const video = await action()
      if (kind === 'reload') {
        adopt(video)
        setStale(false)
        setOpenKey(null)
      } else {
        follow(video)
        notifyDone(video.published_at ? 'Đã giao video cho sinh viên.' : 'Đã ngừng giao video.')
      }
    } catch (err) {
      notifyFailed(apiErrorMessage(err, fallback))
    } finally {
      setBusy(null)
    }
  }

  // Asks for a picture or a render, then reads the detail to follow the job.
  async function startJob(kind: 'draw' | 'render', job: JobType, ask: () => Promise<unknown>, fallback: string) {
    setBusy(kind)
    try {
      await ask()
    } catch (err) {
      // A quota (429) or a job in the way (409) comes with the server's own Vietnamese wording.
      notifyFailed(apiErrorMessage(err, fallback))
      setBusy(null)
      return
    }
    // The job is queued whatever happens next, so it is followed even if this read fails.
    awaitJob(job)
    try {
      follow(await getVideo(videoId))
    } catch {
      if (QUEUED_TEXT[job]) notifyDone(QUEUED_TEXT[job])
    } finally {
      setBusy(null)
    }
  }

  // Every change is saved at once, as in the module editor: there is no unsaved draft to forget.
  async function save(list: SceneDraft[] = scenes, done = 'Đã lưu.') {
    setBusy('save')
    try {
      adopt(await saveVideoScenes(videoId, base.version, toEdits(list)))
      setStale(false)
      setOpenKey(null)
      notifyDone(done)
    } catch (err) {
      // A 409 here is a newer draft saved elsewhere: saving again would be refused the same way.
      setStale(axios.isAxiosError(err) && err.response?.status === 409)
      notifyFailed(apiErrorMessage(err, 'Lưu thay đổi thất bại'))
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

  async function startRender() {
    if (saved && saved.render_warnings.length > 0
      && !await confirmAction({ title: 'Vẫn dựng video?', description: saved.render_warnings.join('\n'), action: 'Dựng video' })) return
    void startJob('render', 'video_render', () => renderVideo(videoId), 'Dựng video thất bại')
  }


  return (
    <AppShell {...titleProps}>
      <div className="video-editor">
        {runningJob && <p role="status" className="status-pill" data-status="processing">{JOB_TEXT[runningJob]}</p>}

        <ol className="video-scenes" aria-label="Các cảnh">
          {scenes.map((s, index) => (
            <VideoSceneEditor
              key={s.key}
              scene={s}
              number={index + 1}
              count={scenes.length}
              slide={{
                videoId,
                limiter,
                picturesDrawn,
                origin: s.fromIndex === null ? undefined : saved.scenes[s.fromIndex],
                changed: s.fromIndex !== null && JSON.stringify(toEdits([s])[0]) !== savedEdits[s.fromIndex],
              }}
              canDraw={canStartJob && openKey === null}
              nextKey={nextKey}
              onChange={(change) => editScene(s.key, change)}
              onMove={(by) => moveScene(index, by)}
              onRemove={() => void removeScene(index)}
              onDraw={(sceneIndex) => draw([sceneIndex])}
              open={openKey === s.key}
              locked={busy !== null || (openKey !== null && openKey !== s.key)}
              saving={busy === 'save'}
              onOpen={() => setOpenKey(s.key)}
              onSave={() => void save()}
              onCancel={cancel}
            />
          ))}
        </ol>

        <button type="button" onClick={addScene} disabled={scenes.length >= MAX_SCENES || openKey !== null || busy !== null}
          className="ui-button">
          Thêm cảnh
        </button>

        {(overLong || stale) && (
          <div className="video-save">
            {overLong && (
              <p className="text-sm danger-text">Quá {formatMinutes(saved.max_seconds)} phút: hãy rút ngắn lời thoại trước khi dựng.</p>
            )}
            {stale && (
              <button type="button" onClick={reload} disabled={busy !== null} className="ui-button w-fit">
                {busy === 'reload' ? 'Đang tải…' : 'Tải lại bản nháp'}
              </button>
            )}
          </div>
        )}

        <section className="video-step" aria-label="Video">
          {saved.render_problems.length > 0 && (
            <ul className="video-problems" aria-label="Chưa dựng được vì">
              {saved.render_problems.map((problem, i) => <li key={`${i}-${problem}`} className="text-sm danger-text">{problem}</li>)}
            </ul>
          )}
          {saved.render_warnings.map((warning, i) => <p key={`${i}-${warning}`} className="doc-warning">{warning}</p>)}
          <div className="flashcard-actions">
            <button
              type="button"
              onClick={() => draw()}
              disabled={!canStartJob || toDraw === 0}
              className="ui-button"
            >
              {busy === 'draw' ? 'Đang gửi…' : 'Vẽ hình'}
            </button>
            <button
              type="button"
              onClick={() => void startRender()}
              disabled={!canStartJob || saved.render_problems.length > 0}
              className="ui-button primary"
            >
              {busy === 'render' ? 'Đang gửi…' : rendered === null ? 'Dựng video' : 'Dựng lại video'}
            </button>
            <button
              type="button"
              onClick={() => void act('publish', () => publishVideo(videoId, !published), published ? 'Ngừng giao thất bại' : 'Giao video thất bại')}
              disabled={busy !== null || (!published && rendered === null)}
              className="ui-button"
            >
              {busy === 'publish' ? 'Đang xử lý…' : published ? 'Ngừng giao' : 'Giao cho sinh viên'}
            </button>
            {deleteButton}
          </div>
          {rendered !== null && (
            <>
              {(saved.draft_version > rendered || dirty) && (
                <p className="doc-warning">
                  Bản nháp đã sửa sau lần dựng gần nhất: video dưới đây là bản cũ
                  {published ? ', và sinh viên vẫn xem bản này' : ''}. Dựng lại để cập nhật.
                </p>
              )}
              <RenderedVideo videoId={videoId} renderedVersion={rendered} captions={saved.captions} language={saved.output_language} />
              {saved.duration_seconds !== null && (
                <p className="course-hint">Dài {formatMinutes(saved.duration_seconds)} phút.</p>
              )}
            </>
          )}
        </section>
      </div>
    </AppShell>
  )
}
