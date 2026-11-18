import axios from 'axios'
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import Markdown, { type Components } from 'react-markdown'
import { Link, useParams } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { MoodleAccountSection, type MoodleAccountState } from '../components/MoodleAccountSection'
import { modelMarkdownProps } from '../lib/markdownMath'
import { confirmAction } from '../lib/confirm'
import {
  chatEditSchema,
  examplesContentSchema,
  explanationsContentSchema,
  markdownContentSchema,
  moduleTitleSchema,
  publishToMoodleSchema,
  questionEditSchema,
  slidesContentSchema,
  type ChatEditFormValues,
  type ExamplesContentFormValues,
  type ExplanationsContentFormValues,
  type MarkdownContentFormValues,
  type ModuleTitleFormValues,
  type PublishToMoodleFormValues,
  type QuestionEditFormValues,
  type QuestionEditInput,
  type SlidesContentFormValues,
} from '../lib/formSchemas'
import {
  api,
  apiErrorMessage,
  blobErrorMessage,
  chatEditModule,
  getMoodleSections,
  getModule,
  publishToMoodle,
  updateComponentContent,
  updateModuleTitle,
  updateQuestion,
  type ExampleItem,
  type ExplanationItem,
  type ModuleComponentInfo,
  type ModuleDetail,
  type MoodleSection,
  type PublishToMoodleResult,
  type QuestionCheck,
  type QuestionInfo,
} from '../api'

type Citation = NonNullable<ModuleDetail['claim_citations']>[number]
interface OpenedSource { citation: Citation; number: number }

// The passage a lecturer is checking a statement against. Opening it does not cover the statement:
// on a wide screen it is the column beside the text, on a phone a sheet over the lower half.
const SourceContext = createContext<{ opened: OpenedSource | null; open: (source: OpenedSource, from: HTMLElement) => void }>({
  opened: null, open: () => {},
})

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
      ) : (
        <p className="module-source-empty">Bấm số trích dẫn như [1] trong bài để xem đoạn nguồn ở đây.</p>
      )}
    </aside>
  )
}

const COMPONENT_LABEL: Record<string, string> = {
  content: 'Nội dung',
  slides: 'Slide bài giảng',
  example: 'Ví dụ minh họa',
  quiz: 'Câu hỏi kiểm tra',
  practice: 'Câu hỏi luyện tập',
  explanation: 'Giải thích đáp án',
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
  const closeSource = useCallback(() => {
    setOpened(null)
    // Back to the marker the lecturer opened it from, so reading goes on where it stopped.
    openedFrom.current?.focus()
  }, [])

  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
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
    <AppShell title="Xem trước mô-đun">
      <SourceContext.Provider value={{ opened, open: openSource }}>
        <div className="module-layout">
          <nav aria-label="Các phần của mô-đun" className="module-parts">
            <ol>
              <li><a href="#part-objective">Mục tiêu</a></li>
              {module.components.map((c) => (
                <li key={c.id}><a href={`#part-${c.type}`}>{COMPONENT_LABEL[c.type] ?? c.type}</a></li>
              ))}
              {!module.is_control && <li><a href="#part-ai-edit">Chỉnh sửa bằng AI</a></li>}
              {!module.is_control && <li><a href="#part-publish">Xuất bản</a></li>}
            </ol>
          </nav>
          <div className="module-main">
            <ModuleHeader module={module} onSaved={refresh} />
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
  const [error, setError] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    reset,
    control,
    formState: { errors, isSubmitting: sending },
  } = useForm<ChatEditFormValues>({ resolver: zodResolver(chatEditSchema), defaultValues: { message: '' } })
  const blank = !useWatch({ control, name: 'message' }).trim()

  async function send({ message }: ChatEditFormValues) {
    setError(null)
    let applied = false
    try {
      const result = await chatEditModule(module.id, message, sessionId)
      setSessionId(result.session_id)
      setTurns((previous) => [...previous, { message, reply: result.reply }])
      reset()
      applied = result.applied
      if (applied) await onSaved()
    } catch (err) {
      setError(applied
        ? 'Đã áp dụng chỉnh sửa nhưng không tải lại được mô-đun. Vui lòng tải lại trang.'
        : apiErrorMessage(err, 'Chỉnh sửa thất bại. Vui lòng thử lại.'))
    }
  }

  return <section id="part-ai-edit" className="module-ai-edit">
    <h2 className="module-part-title">Chỉnh sửa bằng AI</h2>
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
    {error && <p role="alert" className="mt-2 text-sm danger-text">{error}</p>}
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
  }, [courseId, moodleCourseId, courseScope, onRefresh, accountFailed])

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
    setError(null)
    setResult(null)
    try {
      // "Thêm section mới" gửi 1 số section chưa tồn tại (tổng số section
      // hiện có) -- plugin tự tạo mới đúng section đó khi push.
      const target = addNewSection ? sections.length : sectionNum
      setResult(await publishToMoodle(moduleId, target, publishedAt !== null))
    } catch (err) {
      setAuthorizedCourse(null)
      setError(apiErrorMessage(err, 'Xuất bản lên Moodle thất bại'))
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

      {result && (
        <p role="status" className="mt-3 text-sm success-text">
          Đã tạo trên khóa học Moodle #{result.moodle_course_id}: sách nội dung
          {result.practice_cmid ? ', bài luyện tập' : ''}
          {result.quiz_cmid ? ', quiz' : ''}.
        </p>
      )}
      {error && !(accountFailed && !canPublish) && <p className="mt-3 text-sm danger-text">{error}</p>}
    </section>
  )
}

function ModuleHeader({ module, onSaved }: { module: ModuleDetail; onSaved: () => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting: saving },
  } = useForm<ModuleTitleFormValues>({ resolver: zodResolver(moduleTitleSchema), defaultValues: { title: module.title } })
  const [error, setError] = useState('')

  async function handleSave({ title }: ModuleTitleFormValues) {
    setError('')
    try {
      await updateModuleTitle(module.id, title)
      await onSaved()
      setEditing(false)
    } catch (err) {
      setError(apiErrorMessage(err, SAVE_FAILED))
    }
  }

  return (
    <div id="part-objective" className="module-header">
      {editing ? (
        <form onSubmit={handleSubmit(handleSave)} noValidate className="flex flex-wrap items-center gap-3">
          <input
            {...register('title')}
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
              setError('')
              setEditing(false)
            }}
            className="ui-button"
          >
            Hủy
          </button>
          {errors.title && <p className="basis-full text-sm text-red-600">{errors.title.message}</p>}
          {error && <p role="alert" className="basis-full text-sm danger-text">{error}</p>}
        </form>
      ) : (
        <button
          onClick={() => {
            reset({ title: module.title })
            setEditing(true)
          }}
          className="text-left hover:text-accent"
        >
          <h2 className="module-title">{module.title}</h2>
        </button>
      )}

      {!!module.generation_models?.length && (
        <p className="mt-2 text-sm text-gray-600">
          Mô hình AI: {[...new Set(module.generation_models.map((call) => `${call.requested_alias} → ${call.served_model}`))].join(', ')}
        </p>
      )}
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

// The explanation a learner sees for each question, by position: the one in the Explanation
// component, which is the teacher's edited version and the one sent to Moodle.
function explanationsByQuestion(module: ModuleDetail, type: string): Record<number, string> {
  const component = module.components.find((c) => c.type === 'explanation')
  const items = (component?.content.items as ExplanationItem[] | undefined) ?? []
  return Object.fromEntries(
    items.filter((i) => i.component === type).map((i) => [i.position, i.explanation_text]),
  )
}

function ComponentCard({
  moduleId,
  moduleTitle,
  component,
  citations,
  explanations,
  onSaved,
}: {
  moduleId: string
  moduleTitle: string
  component: ModuleComponentInfo
  citations: ModuleDetail['claim_citations']
  explanations: Record<number, string>
  onSaved: () => Promise<void>
}) {
  return (
    <section id={`part-${component.type}`} className="module-part">
      <h2 className="module-part-title">{COMPONENT_LABEL[component.type] ?? component.type}</h2>
      {component.type === 'content' && (
        <>
          <MarkdownFieldEditor moduleId={moduleId} component={component} citations={citations} onSaved={onSaved} />
          {!component.content.source_map && citations && citations.length > 0 && (
            <ul className="mt-4 space-y-3 border-t border-gray-200 pt-4 text-sm">
              {citations.map((citation, i) => (
                <li key={i}>
                  <p>{citation.claim}</p>
                  {citation.resolution === 'resolved' ? (
                    <details className="mt-1">
                      <summary className="cursor-pointer text-sm text-accent hover:underline">
                        {citation.locator}
                      </summary>
                      <p className="mt-2 whitespace-pre-wrap rounded bg-gray-50 p-3">{citation.chunk_content}</p>
                    </details>
                  ) : (
                    <p className="mt-1 text-sm text-gray-500">
                      {citation.locator}: <span>{citation.resolution === 'ambiguous' ? 'trùng nhiều đoạn nguồn, không xác định được' : 'không tìm thấy đoạn nguồn'}</span>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {component.type === 'slides' && (
        <>
          <SlidesEditor moduleId={moduleId} component={component} onSaved={onSaved} />
          <SlidesDownload moduleId={moduleId} moduleTitle={moduleTitle} />
        </>
      )}
      {component.type === 'example' && (
        <ExamplesEditor moduleId={moduleId} component={component} onSaved={onSaved} />
      )}
      {component.type === 'explanation' && (
        <ExplanationEditor moduleId={moduleId} component={component} onSaved={onSaved} />
      )}
      {(component.type === 'quiz' || component.type === 'practice') && (
        <QuizDisplay moduleId={moduleId} component={component} explanations={explanations} onSaved={onSaved} />
      )}
    </section>
  )
}

// The API needs the access token, so a file comes down as a blob rather than through a plain link.
async function saveFile(path: string, filename: string) {
  const { data } = await api.get<Blob>(path, { responseType: 'blob' })
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

// For an LMS the system is not connected to, or a Moodle course nobody linked: one zip the
// lecturer uploads there as a SCORM activity, which records the quiz score.
function ScormDownload({ moduleId, moduleTitle }: { moduleId: string; moduleTitle: string }) {
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  async function download() {
    setError(null)
    setDownloading(true)
    try {
      await saveFile(`/modules/${moduleId}/scorm.zip`, `${moduleTitle} - SCORM.zip`)
    } catch (err) {
      setError(await blobErrorMessage(err, 'Tải gói SCORM thất bại'))
    } finally {
      setDownloading(false)
    }
  }
  return (
    <section className="module-publish" aria-labelledby="scorm-title">
      <h2 id="scorm-title" className="module-part-title">Tải gói SCORM</h2>
      <p className="text-sm muted mb-3">
        Tệp zip chuẩn SCORM 1.2 gồm nội dung, bài luyện tập và bài kiểm tra. Moodle hoặc LMS khác nhập được tệp này và ghi lại điểm.
      </p>
      <button type="button" className="ui-button" disabled={downloading} onClick={() => void download()}>
        {downloading ? 'Đang tạo gói…' : 'Tải gói SCORM (.zip)'}
      </button>
      {error && <p role="alert" className="mt-3 text-sm danger-text">{error}</p>}
    </section>
  )
}

// At the end of the slides it exports: the file carries their text only, so it is a handout for
// whoever wants one, not the main action of the page.
function SlidesDownload({ moduleId, moduleTitle }: { moduleId: string; moduleTitle: string }) {
  const [error, setError] = useState<string | null>(null)
  async function download() {
    setError(null)
    try {
      await saveFile(`/modules/${moduleId}/slides.pptx`, `${moduleTitle}.pptx`)
    } catch (err) {
      setError(await blobErrorMessage(err, 'Tải slide thất bại'))
    }
  }
  return <div className="slides-download">
    <button type="button" className="link-action" onClick={() => void download()}>Tải slide (PPTX)</button>
    {error && <p role="alert" className="mt-2 text-sm danger-text">{error}</p>}
  </div>
}

const SAVE_FAILED = 'Không thể lưu thay đổi. Vui lòng thử lại.'

function SaveCancelRow({
  editing,
  saving,
  onEdit,
  onSave,
  onCancel,
}: {
  editing: boolean
  saving: boolean
  onEdit: () => void
  onSave: () => Promise<void>
  onCancel: () => void
}) {
  // Here, so that every editor reports a failed save: otherwise the button goes back to "Lưu"
  // with nothing said, and the lecturer moves on and loses the edit.
  const [error, setError] = useState('')
  async function save() {
    setError('')
    try {
      await onSave()
    } catch (err) {
      setError(apiErrorMessage(err, SAVE_FAILED))
    }
  }
  if (!editing) {
    return (
      <button onClick={() => { setError(''); onEdit() }} className="ui-button self-start">
        Sửa
      </button>
    )
  }
  return (
    <>
      <div className="flex gap-3 self-start">
        <button
          onClick={() => void save()}
          disabled={saving}
          className="ui-button primary"
        >
          {saving ? 'Đang lưu…' : 'Lưu'}
        </button>
        <button onClick={() => { setError(''); onCancel() }} className="ui-button">
          Hủy
        </button>
      </div>
      {error && <p role="alert" className="text-sm danger-text">{error}</p>}
    </>
  )
}

// The page has no typography plugin, so each Markdown element gets the look of the rest of the
// card. react-markdown builds React elements and never inserts raw HTML, so generated text
// cannot put markup of its own on the page.
const MARKDOWN: Components = {
  h1: ({ children }) => <h4 className="mb-2 mt-4 text-base font-semibold first:mt-0">{children}</h4>,
  h2: ({ children }) => <h4 className="mb-2 mt-4 text-base font-semibold first:mt-0">{children}</h4>,
  h3: ({ children }) => <h5 className="mb-1 mt-3 font-semibold first:mt-0">{children}</h5>,
  p: ({ children }) => <p className="mb-2">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal pl-5">{children}</ol>,
  li: ({ children }) => <li className="mb-0.5">{children}</li>,
  code: ({ children }) => <code className="rounded bg-gray-100 px-1 font-mono text-sm">{children}</code>,
}

function MarkdownFieldEditor({
  moduleId,
  component,
  citations,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  citations: ModuleDetail['claim_citations']
  onSaved: () => Promise<void>
}) {
  const original = (component.content.markdown as string) ?? ''
  const { opened, open } = useContext(SourceContext)
  const sources = new Map<string, { number: number; citation: Citation }>()
  for (const citation of citations ?? []) {
    if (citation.source_id && !sources.has(citation.source_id)) {
      sources.set(citation.source_id, { number: sources.size + 1, citation })
    }
  }
  const rendered = component.content.source_map ? original.replace(/\[(S[0-9]+)\]/g, (marker, code: string) => {
    const source = sources.get(code)
    return source ? `[${source.number}](#citation-${code})` : marker
  }) : original
  const markdownComponents: Components = {
    ...MARKDOWN,
    a: ({ href, children }) => {
      const source = sources.get((href ?? '').replace(/^#citation-/, ''))
      return source && href?.startsWith('#citation-') ? (
        <button type="button" aria-label={`Nguồn ${source.number}`} aria-pressed={opened?.citation === source.citation}
          className="citation-marker"
          onClick={(event) => open({ citation: source.citation, number: source.number }, event.currentTarget)}>
          [{source.number}]
        </button>
      ) : <a href={href} className="text-accent underline">{children}</a>
    },
  }
  const [editing, setEditing] = useState(false)
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting: saving },
  } = useForm<MarkdownContentFormValues>({ resolver: zodResolver(markdownContentSchema), defaultValues: { markdown: original } })

  async function handleSave({ markdown }: MarkdownContentFormValues) {
    await updateComponentContent(moduleId, component.id, { ...component.content, markdown })
    await onSaved()
    setEditing(false)
  }

  return (
    <div className="flex flex-col gap-3">
      {editing ? (
        <textarea
          aria-label="Nội dung"
          {...register('markdown')}
          rows={10}
          className="w-full rounded border border-gray-300 p-3 font-mono text-sm outline-none focus:border-accent"
        />
      ) : (
        <div className="lesson-text">
          <Markdown {...modelMarkdownProps} components={markdownComponents}>{rendered}</Markdown>
        </div>
      )}
      <SaveCancelRow
        editing={editing}
        saving={saving}
        onEdit={() => {
          reset({ markdown: original })
          setEditing(true)
        }}
        onSave={handleSubmit(handleSave)}
        onCancel={() => {
          reset({ markdown: original })
          setEditing(false)
        }}
      />
    </div>
  )
}

interface Slide {
  title: string
  bullets: string[]
}

function SlidesEditor({
  moduleId,
  component,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  onSaved: () => Promise<void>
}) {
  const originalSlides = ((component.content.slides as Slide[]) ?? []).map((s) => ({ ...s }))
  const formValues = (): SlidesContentFormValues => ({
    slides: originalSlides.map((s) => ({ title: s.title, bulletsText: s.bullets.join('\n') })),
  })
  const [editing, setEditing] = useState(false)
  const {
    register,
    handleSubmit,
    reset,
    control,
    formState: { isSubmitting: saving },
  } = useForm<SlidesContentFormValues>({ resolver: zodResolver(slidesContentSchema), defaultValues: formValues() })
  const editedSlides = useWatch({ control, name: 'slides' })

  async function handleSave(values: SlidesContentFormValues) {
    const slides = values.slides.map((s, i) => ({
      ...originalSlides[i],
      title: s.title,
      bullets: s.bulletsText.split('\n'),
    }))
    await updateComponentContent(moduleId, component.id, { ...component.content, slides })
    await onSaved()
    setEditing(false)
  }

  return (
    <div className="flex flex-col gap-4">
      {originalSlides.map((slide, i) => (
        <div key={i} className="border-l-2 border-gray-200 pl-4">
          {editing ? (
            <>
              <input
                aria-label={`Tiêu đề slide ${i + 1}`}
                {...register(`slides.${i}.title`)}
                className="w-full border-0 border-b border-gray-300 bg-transparent pb-1 font-semibold outline-none focus:border-accent"
              />
              <textarea
                aria-label={`Nội dung slide ${i + 1}`}
                {...register(`slides.${i}.bulletsText`)}
                rows={Math.max(2, (editedSlides[i]?.bulletsText ?? '').split('\n').length)}
                className="mt-2 w-full rounded border border-gray-300 p-2 text-sm outline-none focus:border-accent"
              />
            </>
          ) : (
            <>
              <div className="font-semibold">{slide.title}</div>
              <ul className="mt-1 list-disc pl-5 text-sm text-gray-700">
                {slide.bullets.filter(Boolean).map((b, j) => (
                  <li key={j}>{b}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      ))}
      <SaveCancelRow
        editing={editing}
        saving={saving}
        onEdit={() => {
          reset(formValues())
          setEditing(true)
        }}
        onSave={handleSubmit(handleSave)}
        onCancel={() => {
          reset(formValues())
          setEditing(false)
        }}
      />
    </div>
  )
}

function ExamplesEditor({
  moduleId,
  component,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  onSaved: () => Promise<void>
}) {
  const original = (component.content.examples as ExampleItem[] | undefined) ?? []
  const formValues = (): ExamplesContentFormValues => ({
    examples: original.map((e) => ({ context: e.context, analysis: e.analysis })),
  })
  const [editing, setEditing] = useState(false)
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting: saving },
  } = useForm<ExamplesContentFormValues>({ resolver: zodResolver(examplesContentSchema), defaultValues: formValues() })

  async function handleSave(values: ExamplesContentFormValues) {
    const examples = original.map((e, i) => ({ ...e, ...values.examples[i] }))
    await updateComponentContent(moduleId, component.id, { examples })
    await onSaved()
    setEditing(false)
  }

  return (
    <div className="flex flex-col gap-6">
      {original.map((example, index) => (
        <div key={index} className="flex flex-col gap-3">
          <h3 className="part-subtitle">Ví dụ {index + 1}</h3>
          {(['context', 'analysis'] as const).map((field) => (
            <div key={field}>
              <div className="part-label">
                {field === 'context' ? 'Bối cảnh' : 'Phân tích'}
              </div>
              {editing ? (
                <textarea
                  aria-label={`${field === 'context' ? 'Bối cảnh' : 'Phân tích'} ví dụ ${index + 1}`}
                  {...register(`examples.${index}.${field}`)}
                  rows={4}
                  className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-accent"
                />
              ) : (
                <p className="lesson-para">{example[field]}</p>
              )}
            </div>
          ))}
        </div>
      ))}
      <SaveCancelRow
        editing={editing}
        saving={saving}
        onEdit={() => {
          reset(formValues())
          setEditing(true)
        }}
        onSave={handleSubmit(handleSave)}
        onCancel={() => {
          reset(formValues())
          setEditing(false)
        }}
      />
    </div>
  )
}

function explanationLabel(item: ExplanationItem): string {
  return item.component === 'quiz' ? `Câu kiểm tra ${item.position + 1}` : `Câu luyện tập ${item.position + 1}`
}

function ExplanationEditor({
  moduleId,
  component,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  onSaved: () => Promise<void>
}) {
  const original = (component.content.items as ExplanationItem[] | undefined) ?? []
  const formValues = (): ExplanationsContentFormValues => ({
    items: original.map((item) => ({ reasoning: item.reasoning, explanation_text: item.explanation_text })),
  })
  const [editing, setEditing] = useState(false)
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting: saving },
  } = useForm<ExplanationsContentFormValues>({ resolver: zodResolver(explanationsContentSchema), defaultValues: formValues() })

  async function handleSave(values: ExplanationsContentFormValues) {
    const items = original.map((item, i) => ({ ...item, ...values.items[i] }))
    await updateComponentContent(moduleId, component.id, { items })
    await onSaved()
    setEditing(false)
  }

  return (
    <div className="flex flex-col gap-6">
      {original.map((item, index) => (
        <div key={`${item.component}-${item.position}`} className="flex flex-col gap-3">
          <h3 className="part-subtitle">{explanationLabel(item)}</h3>
          <div>
            <div className="part-label">
              Giải thích
            </div>
            {editing ? (
              <textarea
                aria-label={`Giải thích ${explanationLabel(item)}`}
                {...register(`items.${index}.explanation_text`)}
                rows={4}
                className="ui-input w-full"
              />
            ) : (
              <p className="lesson-para">{item.explanation_text}</p>
            )}
          </div>
          {/* The reasoning is the AI's own working, for the lecturer only; the explanation above is
              what students get as quiz feedback in Moodle, so it is shown and the reasoning folded. */}
          {editing ? (
            <div>
              <div className="part-label">Lập luận</div>
              <textarea
                aria-label={`Lập luận ${explanationLabel(item)}`}
                {...register(`items.${index}.reasoning`)}
                rows={4}
                className="ui-input w-full"
              />
            </div>
          ) : (
            <details className="reasoning">
              <summary>Lập luận của AI</summary>
              <p className="reasoning-text">{item.reasoning}</p>
            </details>
          )}
        </div>
      ))}
      <SaveCancelRow
        editing={editing}
        saving={saving}
        onEdit={() => {
          reset(formValues())
          setEditing(true)
        }}
        onSave={handleSubmit(handleSave)}
        onCancel={() => {
          reset(formValues())
          setEditing(false)
        }}
      />
    </div>
  )
}

function QuizDisplay({
  moduleId,
  component,
  explanations,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  explanations: Record<number, string>
  onSaved: () => Promise<void>
}) {
  const checks = (component.content.questions as QuestionCheck[] | undefined) ?? []
  const questions = [...component.questions].sort((a, b) => a.position - b.position)
  const failed = checks.filter((c) => !c.passed && c.fail_reason)
  const selfChecked = component.content.self_checked !== false

  return (
    <div className="flex flex-col gap-6">
      {questions.map((question) => {
        const passed = checks[question.position]?.passed
        return (
          <div key={question.id} className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="part-subtitle">Câu {question.position + 1}</h3>
              <span
                className={`check-badge ${
                  !passed
                    ? 'bg-red-100 text-red-800'
                    : selfChecked
                      ? 'bg-green-100 text-green-800'
                      : 'bg-gray-100 text-gray-700'
                }`}
              >
                {!passed ? 'Chưa khớp nguồn' : selfChecked ? 'Đã kiểm với nguồn' : 'Chưa kiểm'}
              </span>
            </div>
            <QuestionEditor moduleId={moduleId} question={question}
              explanation={explanations[question.position] || question.explanation || ''} onSaved={onSaved} />
          </div>
        )
      })}
      {questions.length === 0 && (
        <p className="text-sm text-gray-500">
          Chưa tạo được câu hỏi hợp lệ
          {failed.length > 0 ? ` (${failed.map((c) => c.fail_reason).join('; ')})` : ''}.
        </p>
      )}
    </div>
  )
}

function QuestionEditor({ moduleId, question, explanation, onSaved }: {
  moduleId: string
  question: QuestionInfo
  explanation: string
  onSaved: () => Promise<void>
}) {
  const formValues = (): QuestionEditInput => ({
    stem: question.stem,
    choices: question.choices.options,
    answer_key: String(question.answer_key),
    explanation,
  })
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting: saving },
  } = useForm<QuestionEditInput, unknown, QuestionEditFormValues>({
    resolver: zodResolver(questionEditSchema),
    defaultValues: formValues(),
  })
  async function save(values: QuestionEditFormValues) {
    setError(null)
    try {
      await updateQuestion(moduleId, question.id, values)
      await onSaved()
      setEditing(false)
    } catch (err) {
      setError(apiErrorMessage(err, 'Lưu câu hỏi thất bại'))
    }
  }
  return <div className="flex flex-col gap-3">
    {editing ? <form onSubmit={handleSubmit(save)} noValidate className="flex flex-col gap-3">
      <label>Câu hỏi<textarea aria-label="Câu hỏi" {...register('stem')} className="w-full rounded border border-gray-300 p-2" /></label>
      {errors.stem && <p className="text-sm text-red-600">{errors.stem.message}</p>}
      {question.choices.options.map((_, i) => <div key={i} className="flex flex-wrap items-center gap-2">
        <input type="radio" value={String(i)} aria-label={`Đáp án ${String.fromCharCode(65 + i)}`} {...register('answer_key')} />
        <input aria-label={`Phương án ${String.fromCharCode(65 + i)}`} {...register(`choices.${i}`)} className="min-w-0 flex-1 rounded border border-gray-300 p-2" />
        {errors.choices?.[i] && <p className="basis-full text-sm text-red-600">{errors.choices[i].message}</p>}
      </div>)}
      <label>Giải thích<textarea aria-label="Giải thích" {...register('explanation')} className="w-full rounded border border-gray-300 p-2" /></label>
      <div className="flex gap-3">
        <button type="submit" disabled={saving} className="text-sm font-semibold text-accent">{saving ? 'Đang lưu…' : 'Lưu'}</button>
        <button type="button" disabled={saving} onClick={() => setEditing(false)} className="text-sm text-gray-500">Hủy</button>
      </div>
    </form> : <>
      <p className="question-stem">{question.stem}</p>
      <ul className="choice-list">{question.choices.options.map((choice, i) => <li key={i} className={String(i) === question.answer_key ? 'choice-key' : undefined}>{String.fromCharCode(65 + i)}. {choice}{String(i) === question.answer_key ? ' ✓' : ''}</li>)}</ul>
      {explanation && <p className="text-sm text-gray-500"><span className="font-semibold">Giải thích: </span>{explanation}</p>}
      <SaveCancelRow editing={false} saving={saving} onSave={handleSubmit(save)} onCancel={() => setEditing(false)} onEdit={() => {
        reset(formValues())
        setError(null)
        setEditing(true)
      }} />
    </>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
  </div>
}
