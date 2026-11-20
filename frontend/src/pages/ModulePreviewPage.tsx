import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useParams } from 'react-router-dom'
import { Ellipsis, Pencil } from 'lucide-react'
import { AppShell } from '../components/AppShell'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../components/ui/dropdown-menu'
import { MoodleAccountSection, type MoodleAccountState } from '../components/MoodleAccountSection'
import { confirmAction } from '../lib/confirm'
import { notifyDone, notifyFailed } from '../lib/notify'
import {
  chatEditSchema,
  moduleTitleSchema,
  publishToMoodleSchema,
  type ChatEditFormValues,
  type ModuleTitleFormValues,
  type PublishToMoodleFormValues,
} from '../lib/formSchemas'
import {
  apiErrorMessage,
  chatEditModule,
  getMoodleSections,
  getModule,
  publishToMoodle,
  updateModuleTitle,
  type ModuleDetail,
  type MoodleSection,
  type PublishToMoodleResult,
} from '../api'
import { ComponentCard, ScormDownload } from '../components/ModuleParts'
import { COMPONENT_LABEL, explanationsByQuestion, SAVE_FAILED, SourceContext, type OpenedSource } from '../lib/moduleParts'

function SourcePanel({ opened, onClose }: { opened: OpenedSource | null; onClose: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { if (opened) heading.current?.focus() }, [opened])
  return (
    <aside role="dialog" aria-modal="false" aria-label="Nguồn trích dẫn" className="module-source" data-open={opened ? 'true' : 'false'}>
      {opened ? (
        <>
          <div className="module-source-head">
            <h2 ref={heading} tabIndex={-1} className="module-source-title">
              Nguồn [{opened.number}]
              <span className="module-source-place">{opened.citation.locator}</span>
            </h2>
            <button type="button" aria-label="Đóng nguồn" onClick={onClose} className="link-button">Đóng</button>
          </div>
          <p className="module-source-label">Câu đang kiểm</p>
          <p className="module-source-claim"><mark>{opened.citation.claim}</mark></p>
          <p className="module-source-label">Đoạn trong tài liệu</p>
          {opened.citation.resolution === 'resolved'
            ? <p className="module-source-passage">{opened.citation.chunk_content}</p>
            : <p className="muted small">{opened.citation.resolution === 'ambiguous'
              ? 'Câu này khớp nhiều đoạn nguồn. Đối chiếu tay trước khi xuất bản.'
              : 'Không tìm thấy đoạn nguồn. Sửa hoặc bỏ trích dẫn trước khi xuất bản.'}</p>}
        </>
      ) : null}
    </aside>
  )
}

export function ModulePreviewPage() {
  const { moduleId } = useParams<{ moduleId: string }>()
  const [module, setModule] = useState<ModuleDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [opened, setOpened] = useState<OpenedSource | null>(null)
  const openedFrom = useRef<HTMLElement | null>(null)
  const openSource = useCallback((source: OpenedSource, from: HTMLElement) => {
    openedFrom.current = from
    setOpened(source)
  }, [])
  // Each editor swaps its "Sửa" button for fields and back, and the browser drops the keyboard focus
  // on the page body when the focused button goes. Focus is put back in the same editor instead.
  useEffect(() => {
    // Taken while the element is still in the page: a removed node no longer has its ancestors.
    let last: { element: HTMLElement; scope: Element | null } | null = null
    const remember = (e: FocusEvent) => {
      const element = e.target as HTMLElement
      last = { element, scope: element.closest('[data-edit-scope]') }
    }
    const observer = new MutationObserver(() => {
      if (!last?.scope?.isConnected || last.element.isConnected || document.activeElement !== document.body) return
      const { scope } = last
      last = null
      ;(scope.querySelector<HTMLElement>('textarea, input, select') ?? scope.querySelector<HTMLElement>('[data-edit-trigger]'))?.focus()
    })
    document.addEventListener('focusin', remember)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => { document.removeEventListener('focusin', remember); observer.disconnect() }
  }, [])
  const closeSource = useCallback(() => {
    setOpened(null)
    // Back to the marker the lecturer opened it from, so reading goes on where it stopped.
    openedFrom.current?.focus()
  }, [])

  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [renaming, setRenaming] = useState(false)
  const refresh = useCallback(async () => {
    if (!moduleId) return
    setModule(await getModule(moduleId))
  }, [moduleId])

  // The first load sets state from promise callbacks, and drops a result that
  // arrives after the page has gone; refresh() is for reloading after an edit.
  useEffect(() => {
    let cancelled = false
    const first = moduleId
      ? getModule(moduleId).then(
          (loaded) => {
            if (!cancelled) setModule(loaded)
          },
          (err) => {
            // Only a 404 means the module is gone; anything else is a failed load, which must
            // not read as "not found" to a lecturer whose module is still there.
            if (!cancelled && !(axios.isAxiosError(err) && err.response?.status === 404)) setLoadFailed(true)
          },
        )
      : Promise.resolve()
    first.finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [moduleId, attempt])

  function retryLoad() {
    setLoading(true)
    setLoadFailed(false)
    setAttempt((n) => n + 1)
  }

  if (loading) {
    return (
      <AppShell title="Mô-đun">
        <p className="text-sm text-gray-500">Đang tải…</p>
      </AppShell>
    )
  }

  if (loadFailed) {
    return (
      <AppShell title="Mô-đun">
        <p role="alert" className="text-sm danger-text">Không tải được mô-đun.</p>
        <button type="button" onClick={retryLoad} className="ui-button mt-3">Thử lại</button>
      </AppShell>
    )
  }

  if (!module) {
    return (
      <AppShell title="Mô-đun">
        <p className="text-sm danger-text">Không tìm thấy mô-đun.</p>
      </AppShell>
    )
  }

  return (
    <AppShell title={`Mô-đun: ${module.title}`} actions={
      // As on the course list: renaming sits behind the page's "⋯" menu, not as a link under the title.
      <DropdownMenu>
        <DropdownMenuTrigger className="icon-button" aria-label={`Thao tác khác với ${module.title}`}>
          <Ellipsis aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="doc-menu">
          <DropdownMenuItem onSelect={() => setRenaming(true)}><Pencil aria-hidden="true" />Đổi tên</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    }>
      <SourceContext.Provider value={{ opened, open: openSource }}>
        <div className="module-layout">
          <nav aria-label="Các phần của mô-đun" className="module-parts">
            <ol>
              <li><a href="#part-objective">Mục tiêu</a></li>
              {module.components.map((c) => (
                <li key={c.id}><a href={`#part-${c.type}`}>{COMPONENT_LABEL[c.type] ?? c.type}</a></li>
              ))}
              {!module.is_control && <li><a href="#part-ai-edit">Sửa theo yêu cầu</a></li>}
              {!module.is_control && <li><a href="#part-publish">Xuất bản</a></li>}
            </ol>
          </nav>
          <div className="module-main">
            <ModuleHeader module={module} onSaved={refresh} editing={renaming} onEditingChange={setRenaming} />
            {module.is_control && (
              <p className="mt-2 text-sm text-amber-700">Bản đối chứng cho nghiên cứu, không dùng tài liệu</p>
            )}
            <div className="module-sheet">
              {module.components.map((c) => (
                <ComponentCard
                  key={c.id}
                  moduleId={module.id}
                  moduleTitle={module.title}
                  component={c}
                  citations={module.claim_citations}
                  explanations={explanationsByQuestion(module, c.type)}
                  onSaved={refresh}
                />
              ))}
            </div>
            {/* After the lesson: the lecturer knows what to change only once it has been read. */}
            {!module.is_control && <ModuleChatPanel key={module.id} module={module} onSaved={refresh} />}
            {!module.is_control && <div id="part-publish"><PublishToMoodleSection
              moduleId={module.id}
              courseId={module.course_id}
              moodleCourseId={module.moodle_course_id}
              publishedAt={module.moodle_published_at ?? null}
              onRefresh={refresh}
            /><ScormDownload moduleId={module.id} moduleTitle={module.title} /></div>}
          </div>
          <SourcePanel opened={opened} onClose={closeSource} />
        </div>
      </SourceContext.Provider>
    </AppShell>
  )
}

function ModuleChatPanel({ module, onSaved }: { module: ModuleDetail; onSaved: () => Promise<void> }) {
  const [sessionId, setSessionId] = useState<string>()
  const [turns, setTurns] = useState<{ message: string; reply: string }[]>([])
  const {
    register,
    handleSubmit,
    reset,
    control,
    formState: { errors, isSubmitting: sending },
  } = useForm<ChatEditFormValues>({ resolver: zodResolver(chatEditSchema), defaultValues: { message: '' } })
  const blank = !useWatch({ control, name: 'message' }).trim()

  async function send({ message }: ChatEditFormValues) {
    let applied = false
    try {
      const result = await chatEditModule(module.id, message, sessionId)
      setSessionId(result.session_id)
      setTurns((previous) => [...previous, { message, reply: result.reply }])
      reset()
      applied = result.applied
      if (applied) await onSaved()
    } catch (err) {
      notifyFailed(applied
        ? 'Đã áp dụng chỉnh sửa nhưng không tải lại được mô-đun. Vui lòng tải lại trang.'
        : apiErrorMessage(err, 'Chỉnh sửa thất bại. Vui lòng thử lại.'))
    }
  }

  return <section id="part-ai-edit" className="module-ai-edit">
    <h2 className="module-part-title">Sửa theo yêu cầu</h2>
    <div aria-live="polite" className="mt-3 space-y-3 text-sm">
      {turns.map((turn, i) => <div key={i}>
        <p className="text-gray-500">{turn.message}</p>
        <p className="mt-1 whitespace-pre-wrap">{turn.reply}</p>
      </div>)}
    </div>
    <form onSubmit={handleSubmit(send)} noValidate className="mt-3 flex flex-col gap-2">
      <label htmlFor="module-chat-message" className="chat-label">Yêu cầu chỉnh sửa</label>
      <textarea id="module-chat-message" maxLength={4000} rows={3} disabled={sending}
        placeholder="Ví dụ: rút gọn slide 3, làm câu quiz 2 khó hơn"
        {...register('message')} className="ui-input w-full" />
      {errors.message && <p className="text-sm danger-text">{errors.message.message}</p>}
      <button type="submit" disabled={sending || blank}
        className="ui-button primary w-fit">
        {sending ? 'Đang chỉnh sửa…' : 'Gửi yêu cầu'}
      </button>
    </form>
  </section>
}

function PublishToMoodleSection({
  moduleId,
  courseId,
  moodleCourseId,
  publishedAt,
  onRefresh,
}: {
  moduleId: string
  courseId: string
  moodleCourseId: number | null
  publishedAt: string | null
  onRefresh: () => Promise<void>
}) {
  const [result, setResult] = useState<PublishToMoodleResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sections, setSections] = useState<MoodleSection[]>([])
  const [authorizedCourse, setAuthorizedCourse] = useState<string | null>(null)
  // Goes up after a failed publish, so Moodle access is checked again: the form comes back when
  // the refusal was the module's (a question to review) and stays away when access was lost.
  const [accessCheck, setAccessCheck] = useState(0)
  const courseScope = `${courseId}:${moodleCourseId}`
  const canPublish = authorizedCourse === courseScope
  const {
    register,
    handleSubmit,
    control,
    formState: { isSubmitting: publishing },
  } = useForm<PublishToMoodleFormValues>({
    resolver: zodResolver(publishToMoodleSchema),
    defaultValues: { sectionNum: 0, addNewSection: false },
  })
  const addNewSection = useWatch({ control, name: 'addNewSection' })
  // When Moodle itself cannot be reached, the account section above already says so; the failed
  // section list is the same failure and would repeat the message under it.
  const [account, setAccount] = useState<MoodleAccountState>('unknown')
  const accountFailed = account === 'unknown' || account === 'unavailable'

  useEffect(() => {
    // Sections come from Moodle through the lecturer's linked account: until the account answers,
    // or when this environment has no Moodle at all (AWS, LTI only), asking would only fail, and
    // every page view would count a server error against the API's 5xx alarm.
    if (moodleCourseId === null || accountFailed) return
    let cancelled = false
    getMoodleSections(courseId).then(
      (list) => {
        if (cancelled) return
        setSections(list)
        setAuthorizedCourse(courseScope)
      },
      (err) => {
        if (cancelled) return
        setAuthorizedCourse(null)
        setSections([])
        setError(apiErrorMessage(err, 'Không tải được danh sách phần học Moodle'))
        void onRefresh().catch(() => {})
      },
    )
    return () => { cancelled = true }
  }, [courseId, moodleCourseId, courseScope, onRefresh, accountFailed, accessCheck])

  // Asked before the form submits, so the button does not read "Đang xuất bản…" while the question is open.
  async function confirmThenPublish(event: React.FormEvent) {
    event.preventDefault()
    if (publishedAt && !(await confirmAction({
      title: 'Xuất bản lại lên Moodle?',
      description: 'Khóa học Moodle sẽ có thêm một sách nội dung và các bài kiểm tra mới, bản cũ vẫn giữ nguyên.',
      action: 'Xuất bản lại',
    }))) return
    await handleSubmit(handlePublish)()
  }

  async function handlePublish({ sectionNum, addNewSection }: PublishToMoodleFormValues) {
    setResult(null)
    try {
      // "Thêm section mới" gửi 1 số section chưa tồn tại (tổng số section
      // hiện có) -- plugin tự tạo mới đúng section đó khi push.
      const target = addNewSection ? sections.length : sectionNum
      const published = await publishToMoodle(moduleId, target, publishedAt !== null)
      setResult(published)
      notifyDone(`Đã tạo trên khóa học Moodle #${published.moodle_course_id}: sách nội dung${published.practice_cmid ? ', bài luyện tập' : ''}${published.quiz_cmid ? ', quiz' : ''}.`)
    } catch (err) {
      setAuthorizedCourse(null)
      setAccessCheck((n) => n + 1)
      notifyFailed(apiErrorMessage(err, 'Xuất bản lên Moodle thất bại'))
      await onRefresh().catch(() => {})
    }
  }

  if (moodleCourseId === null) {
    return (
      <section className="module-publish" aria-labelledby="publish-title">
        <h2 id="publish-title" className="module-part-title">Xuất bản lên Moodle</h2>
        <p className="text-sm muted">
          Môn học này chưa liên kết với Moodle.{' '}
          <Link to={`/courses/${courseId}`} className="link-action">
            Liên kết ở trang môn học
          </Link>
        </p>
        {error && <p role="alert" className="mt-3 text-sm danger-text">{error}</p>}
      </section>
    )
  }

  return (
    <section className="module-publish" aria-labelledby="publish-title">
      <h2 id="publish-title" className="module-part-title">Xuất bản lên Moodle</h2>
      <MoodleAccountSection onStatus={setAccount} />
      {canPublish && <form onSubmit={confirmThenPublish} noValidate>
      {!addNewSection && sections.length > 0 && (
        <div className="mt-4 mb-3 flex items-center gap-2 text-sm muted">
          <label htmlFor="publish-section">Chủ đề trên Moodle:</label>
          <select
            id="publish-section"
            {...register('sectionNum', { valueAsNumber: true })}
            disabled={result !== null}
            className="ui-select"
          >
            {sections.map((s) => (
              <option key={s.sectionnum} value={s.sectionnum}>
                {s.name || `Chủ đề ${s.sectionnum}`}
              </option>
            ))}
          </select>
        </div>
      )}
      <label className="mb-4 flex items-center gap-2 text-sm muted">
        <input
          type="checkbox"
          {...register('addNewSection')}
          disabled={result !== null}
        />
        Tạo chủ đề mới
      </label>

      <button
        type="submit"
        disabled={publishing || result !== null}
        className="ui-button primary"
      >
        {publishing ? 'Đang xuất bản…' : result !== null ? 'Đã xuất bản lên Moodle' : publishedAt ? 'Xuất bản lại' : 'Xuất bản lên Moodle'}
      </button>
      </form>}

      {publishedAt && !result && (
        <p className="mt-3 text-sm muted">Đã xuất bản lên Moodle lúc {new Date(publishedAt).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })}.</p>
      )}

      {error && !(accountFailed && !canPublish) && <p className="mt-3 text-sm danger-text">{error}</p>}
    </section>
  )
}

function ModuleHeader({ module, onSaved, editing, onEditingChange: setEditing }: {
  module: ModuleDetail; onSaved: () => Promise<void>; editing: boolean; onEditingChange: (editing: boolean) => void
}) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting: saving },
  } = useForm<ModuleTitleFormValues>({ resolver: zodResolver(moduleTitleSchema), defaultValues: { title: module.title } })
  // Opened from the page menu: start from the current title, which a save may have changed.
  useEffect(() => { if (editing) reset({ title: module.title }) }, [editing, module.title, reset])
  async function handleSave({ title }: ModuleTitleFormValues) {
    try {
      await updateModuleTitle(module.id, title)
      await onSaved()
      setEditing(false)
      notifyDone('Đã lưu.')
    } catch (err) {
      notifyFailed(apiErrorMessage(err, SAVE_FAILED))
    }
  }

  return (
    <div id="part-objective" className="module-header" data-edit-scope>
      {editing ? (
        <form onSubmit={handleSubmit(handleSave)} noValidate className="flex flex-wrap items-center gap-3">
          <input
            {...register('title')}
            autoFocus
            aria-label="Tên mô-đun"
            className="module-title flex-1 border-0 border-b-[1.5px] border-accent bg-transparent outline-none"
          />
          <button
            type="submit"
            disabled={saving}
            className="ui-button primary"
          >
            {saving ? 'Đang lưu…' : 'Lưu'}
          </button>
          <button
            type="button"
            onClick={() => {
              reset({ title: module.title })
              setEditing(false)
            }}
            className="ui-button"
          >
            Hủy
          </button>
          {errors.title && <p className="basis-full text-sm danger-text">{errors.title.message}</p>}
        </form>
      ) : null}

      {module.learning_objective && (
        <p className="module-objective">
          <span className="font-semibold">Mục tiêu học tập: </span>
          {module.learning_objective}
        </p>
      )}
      {module.sub_objectives && module.sub_objectives.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-gray-600">
          {module.sub_objectives.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

