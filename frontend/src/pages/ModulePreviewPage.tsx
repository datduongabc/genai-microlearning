import { useCallback, useEffect, useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import Markdown, { type Components } from 'react-markdown'
import { Link, useParams } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import { MoodleAccountSection } from '../components/MoodleAccountSection'
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

const COMPONENT_LABEL: Record<string, string> = {
  content: 'Nội dung',
  slides: 'Slide bài giảng',
  example: 'Ví dụ minh họa',
  quiz: 'Câu hỏi kiểm tra (Quiz)',
  practice: 'Câu hỏi luyện tập (Practice)',
  explanation: 'Giải thích đáp án',
}

export function ModulePreviewPage() {
  const { moduleId } = useParams<{ moduleId: string }>()
  const [module, setModule] = useState<ModuleDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!moduleId) return
    setModule(await getModule(moduleId))
  }, [moduleId])

  // The first load sets state from promise callbacks, and drops a result that
  // arrives after the page has gone; refresh() is for reloading after an edit.
  useEffect(() => {
    let cancelled = false
    const first = moduleId
      ? getModule(moduleId).then((loaded) => {
          if (!cancelled) setModule(loaded)
        })
      : Promise.resolve()
    first.finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [moduleId])

  if (loading) {
    return (
      <AppShell title="Mô-đun">
        <p className="text-sm text-gray-500">Đang tải...</p>
      </AppShell>
    )
  }

  if (!module) {
    return (
      <AppShell title="Mô-đun">
        <p className="text-sm text-red-700">Không tìm thấy mô-đun.</p>
      </AppShell>
    )
  }

  return (
    <AppShell title="Xem trước mô-đun">
      <ModuleHeader module={module} onSaved={refresh} />
      {module.is_control && (
        <p className="mt-2 text-sm text-amber-600">Đối chứng (không RAG) - chỉ dùng cho nghiên cứu</p>
      )}
      {module.components.some((c) => c.type === 'slides') && (
        <button
          className="mt-4 rounded-[3px] bg-navy px-5 py-2.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b]"
          onClick={async () => {
            setDownloadError(null)
            try {
              const { data } = await api.get<Blob>(`/modules/${module.id}/slides.pptx`, { responseType: 'blob' })
              const url = URL.createObjectURL(data)
              const link = document.createElement('a')
              link.href = url
              link.download = `${module.title}.pptx`
              document.body.append(link)
              link.click()
              link.remove()
              URL.revokeObjectURL(url)
            } catch (err) {
              setDownloadError(apiErrorMessage(err, 'Tải slide thất bại'))
            }
          }}
        >
          Tải slide
        </button>
      )}
      {downloadError && <p role="alert" className="mt-3 text-sm text-red-700">{downloadError}</p>}
      {!module.is_control && <ModuleChatPanel key={module.id} module={module} onSaved={refresh} />}
      <div className="mt-8 flex flex-col gap-6">
        {module.components.map((c) => (
          <ComponentCard
            key={c.id}
            moduleId={module.id}
            component={c}
            citations={module.claim_citations}
            explanations={explanationsByQuestion(module, c.type)}
            onSaved={refresh}
          />
        ))}
      </div>
      {!module.is_control && <PublishToMoodleSection
        moduleId={module.id}
        courseId={module.course_id}
        moodleCourseId={module.moodle_course_id}
        onRefresh={refresh}
      />}
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

  return <section className="mt-6 rounded-[3px] border border-gray-200 bg-white p-6">
    <h3 className="text-sm font-semibold">Chỉnh sửa bằng AI</h3>
    <p className="mt-1 text-sm text-gray-500">Nêu một phần cần sửa, ví dụ: “Rút gọn slide 3” hoặc “Làm câu quiz 2 khó hơn”.</p>
    <p className="mt-1 text-xs text-gray-500">Số lượt đã áp dụng: {module.chat_edit_count ?? 0}</p>
    <div aria-live="polite" className="mt-3 space-y-3 text-sm">
      {turns.map((turn, i) => <div key={i}>
        <p className="text-gray-500">{turn.message}</p>
        <p className="mt-1 whitespace-pre-wrap">{turn.reply}</p>
      </div>)}
    </div>
    <form onSubmit={handleSubmit(send)} noValidate className="mt-3 flex flex-col gap-2">
      <label htmlFor="module-chat-message" className="text-sm">Yêu cầu chỉnh sửa</label>
      <textarea id="module-chat-message" maxLength={4000} rows={3} disabled={sending}
        {...register('message')} className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta" />
      {errors.message && <p className="text-xs text-red-600">{errors.message.message}</p>}
      <button type="submit" disabled={sending || blank}
        className="w-fit rounded-[3px] bg-terracotta px-4 py-2 text-sm font-semibold text-white disabled:opacity-60">
        {sending ? 'Đang chỉnh sửa...' : 'Gửi yêu cầu'}
      </button>
    </form>
    {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
  </section>
}

function PublishToMoodleSection({
  moduleId,
  courseId,
  moodleCourseId,
  onRefresh,
}: {
  moduleId: string
  courseId: string
  moodleCourseId: number | null
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

  useEffect(() => {
    if (moodleCourseId === null) return
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
  }, [courseId, moodleCourseId, courseScope, onRefresh])

  async function handlePublish({ sectionNum, addNewSection }: PublishToMoodleFormValues) {
    setError(null)
    setResult(null)
    try {
      // "Thêm section mới" gửi 1 số section chưa tồn tại (tổng số section
      // hiện có) -- plugin tự tạo mới đúng section đó khi push.
      const target = addNewSection ? sections.length : sectionNum
      setResult(await publishToMoodle(moduleId, target))
    } catch (err) {
      setAuthorizedCourse(null)
      setError(apiErrorMessage(err, 'Push to Moodle thất bại'))
      await onRefresh().catch(() => {})
    }
  }

  if (moodleCourseId === null) {
    return (
      <div className="mt-8 border-t border-gray-200 pt-6">
        <p className="text-sm text-gray-600">
          Course của mô-đun này chưa được liên kết với Moodle.{' '}
          <Link to={`/courses/${courseId}`} className="font-semibold text-terracotta hover:underline">
            Vào trang course để liên kết trước
          </Link>
          .
        </p>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      </div>
    )
  }

  return (
    <div className="mt-8 border-t border-gray-200 pt-6">
      <MoodleAccountSection />
      {canPublish && <form onSubmit={handleSubmit(handlePublish)} noValidate>
      {!addNewSection && sections.length > 0 && (
        <div className="mb-3">
          <label className="mr-2 text-sm text-gray-600">Section:</label>
          <select
            {...register('sectionNum', { valueAsNumber: true })}
            disabled={result !== null}
            className="rounded-[3px] border border-gray-300 px-2 py-1 text-sm"
          >
            {sections.map((s) => (
              <option key={s.sectionnum} value={s.sectionnum}>
                {s.name || `Section ${s.sectionnum}`}
              </option>
            ))}
          </select>
        </div>
      )}
      <label className="mb-3 flex items-center gap-2 text-sm text-gray-600">
        <input
          type="checkbox"
          {...register('addNewSection')}
          disabled={result !== null}
        />
        Thêm vào 1 section mới
      </label>

      <button
        type="submit"
        disabled={publishing || result !== null}
        className="rounded-[3px] bg-navy px-5 py-2.5 text-sm font-semibold text-[#f3f1ea] hover:bg-[#28394b] disabled:opacity-60"
      >
        {publishing ? 'Đang đẩy lên Moodle...' : result !== null ? 'Đã đẩy lên Moodle' : 'Push to Moodle'}
      </button>
      </form>}

      {result && (
        <p className="mt-3 text-sm text-green-700">
          Đã đẩy lên Moodle (course id {result.moodle_course_id}): sách nội dung
          {result.practice_cmid ? ', practice' : ''}
          {result.quiz_cmid ? ', quiz' : ''} đã tạo thành công.
        </p>
      )}
      {error && <p className="mt-3 text-sm text-red-700">{error}</p>}
    </div>
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

  async function handleSave({ title }: ModuleTitleFormValues) {
    await updateModuleTitle(module.id, title)
    await onSaved()
    setEditing(false)
  }

  return (
    <div className="border-b border-gray-200 pb-6">
      {editing ? (
        <form onSubmit={handleSubmit(handleSave)} noValidate className="flex flex-wrap items-center gap-3">
          <input
            {...register('title')}
            aria-label="Tên mô-đun"
            className="flex-1 border-0 border-b-[1.5px] border-terracotta bg-transparent font-serif text-xl font-semibold outline-none"
          />
          <button
            type="submit"
            disabled={saving}
            className="rounded-[3px] bg-terracotta px-4 py-1.5 text-xs font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
          >
            {saving ? 'Đang lưu...' : 'Lưu'}
          </button>
          <button
            type="button"
            onClick={() => {
              reset({ title: module.title })
              setEditing(false)
            }}
            className="text-xs font-semibold text-gray-500 hover:text-gray-700"
          >
            Hủy
          </button>
          {errors.title && <p className="basis-full text-xs text-red-600">{errors.title.message}</p>}
        </form>
      ) : (
        <button
          onClick={() => {
            reset({ title: module.title })
            setEditing(true)
          }}
          className="text-left hover:text-terracotta"
        >
          <h2 className="font-serif text-xl font-semibold">{module.title}</h2>
        </button>
      )}

      {!!module.generation_models?.length && (
        <p className="mt-3 text-xs text-gray-600">
          Model sinh: {[...new Set(module.generation_models.map((call) => `${call.requested_alias} → ${call.served_model}`))].join(', ')}
        </p>
      )}
      {module.learning_objective && (
        <p className="mt-3 text-sm text-gray-600">
          <span className="font-semibold text-gray-800">Learning Objective: </span>
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
  component,
  citations,
  explanations,
  onSaved,
}: {
  moduleId: string
  component: ModuleComponentInfo
  citations: ModuleDetail['claim_citations']
  explanations: Record<number, string>
  onSaved: () => Promise<void>
}) {
  return (
    <section className="rounded-[3px] border border-gray-200 bg-white p-6">
      <h3 className="mb-4 text-xs font-semibold uppercase tracking-wider text-terracotta">
        {COMPONENT_LABEL[component.type] ?? component.type}
      </h3>
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
                      <summary className="cursor-pointer text-xs text-terracotta hover:underline">
                        {citation.locator}
                      </summary>
                      <p className="mt-2 whitespace-pre-wrap rounded bg-gray-50 p-3">{citation.chunk_content}</p>
                    </details>
                  ) : (
                    <p className="mt-1 text-xs text-gray-500">
                      {citation.locator} — <span>{citation.resolution === 'ambiguous' ? 'trùng nhiều đoạn nguồn, không xác định được' : 'không tìm thấy đoạn nguồn'}</span>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {component.type === 'slides' && (
        <SlidesEditor moduleId={moduleId} component={component} onSaved={onSaved} />
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
  onSave: () => void
  onCancel: () => void
}) {
  if (!editing) {
    return (
      <button onClick={onEdit} className="text-xs font-semibold text-terracotta hover:underline">
        Sửa
      </button>
    )
  }
  return (
    <div className="flex gap-3">
      <button
        onClick={onSave}
        disabled={saving}
        className="rounded-[3px] bg-terracotta px-4 py-1.5 text-xs font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
      >
        {saving ? 'Đang lưu...' : 'Lưu'}
      </button>
      <button onClick={onCancel} className="text-xs font-semibold text-gray-500 hover:text-gray-700">
        Hủy
      </button>
    </div>
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
  code: ({ children }) => <code className="rounded bg-gray-100 px-1 font-mono text-[13px]">{children}</code>,
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
  const sourceDialog = useRef<HTMLDialogElement>(null)
  const [openedSource, setOpenedSource] = useState<NonNullable<ModuleDetail['claim_citations']>[number] | null>(null)
  const sources = new Map<string, { number: number; citation: NonNullable<ModuleDetail['claim_citations']>[number] }>()
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
        <button type="button" aria-label={`Nguồn ${source.number}`} className="mx-0.5 align-super text-xs font-semibold text-terracotta hover:underline"
          onClick={() => { setOpenedSource(source.citation); sourceDialog.current?.showModal() }}>
          [{source.number}]
        </button>
      ) : <a href={href} className="text-terracotta underline">{children}</a>
    },
  }
  const [editing, setEditing] = useState(false)
  const [saveError, setSaveError] = useState('')
  const {
    register,
    handleSubmit,
    reset,
    formState: { isSubmitting: saving },
  } = useForm<MarkdownContentFormValues>({ resolver: zodResolver(markdownContentSchema), defaultValues: { markdown: original } })

  async function handleSave({ markdown }: MarkdownContentFormValues) {
    setSaveError('')
    try {
      await updateComponentContent(moduleId, component.id, { ...component.content, markdown })
      await onSaved()
      setEditing(false)
    } catch (err) {
      setSaveError(apiErrorMessage(err, 'Không thể lưu nội dung. Kiểm tra mã nguồn trích dẫn rồi thử lại.'))
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {editing ? (
        <textarea
          aria-label="Nội dung"
          {...register('markdown')}
          rows={10}
          className="w-full rounded border border-gray-300 p-3 font-mono text-sm outline-none focus:border-terracotta"
        />
      ) : (
        <div className="text-[15px] leading-relaxed">
          <Markdown components={markdownComponents}>{rendered}</Markdown>
        </div>
      )}
      <dialog ref={sourceDialog} aria-label="Nguồn trích dẫn" className="m-auto w-full max-w-2xl rounded-[3px] p-6 backdrop:bg-black/40">
        <div className="mb-3 flex items-start justify-between gap-4">
          <h4 className="font-semibold">{openedSource?.locator}</h4>
          <button type="button" aria-label="Đóng nguồn" onClick={() => sourceDialog.current?.close()} className="text-sm text-terracotta">Đóng</button>
        </div>
        {openedSource && (openedSource.resolution === 'resolved' ? (
          <p className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed">{openedSource.chunk_content}</p>
        ) : <p className="text-sm text-gray-500">{openedSource?.resolution === 'ambiguous' ? 'trùng nhiều đoạn nguồn, không xác định được' : 'không tìm thấy đoạn nguồn'}</p>)}
      </dialog>
      {saveError && <p role="alert" className="text-sm text-red-600">{saveError}</p>}
      <SaveCancelRow
        editing={editing}
        saving={saving}
        onEdit={() => {
          setSaveError('')
          reset({ markdown: original })
          setEditing(true)
        }}
        onSave={handleSubmit(handleSave)}
        onCancel={() => {
          setSaveError('')
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
                className="w-full border-0 border-b border-gray-300 bg-transparent pb-1 font-semibold outline-none focus:border-terracotta"
              />
              <textarea
                aria-label={`Nội dung slide ${i + 1}`}
                {...register(`slides.${i}.bulletsText`)}
                rows={Math.max(2, (editedSlides[i]?.bulletsText ?? '').split('\n').length)}
                className="mt-2 w-full rounded border border-gray-300 p-2 text-sm outline-none focus:border-terracotta"
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
          <div className="text-sm font-semibold">Ví dụ {index + 1}</div>
          {(['context', 'analysis'] as const).map((field) => (
            <div key={field}>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                {field === 'context' ? 'Bối cảnh' : 'Phân tích'}
              </div>
              {editing ? (
                <textarea
                  aria-label={`${field === 'context' ? 'Bối cảnh' : 'Phân tích'} ví dụ ${index + 1}`}
                  {...register(`examples.${index}.${field}`)}
                  rows={4}
                  className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta"
                />
              ) : (
                <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{example[field]}</p>
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
  return item.component === 'quiz' ? `Quiz - câu ${item.position + 1}` : `Practice - câu ${item.position + 1}`
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
          <div className="text-sm font-semibold">{explanationLabel(item)}</div>
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Lập luận (reasoning)
            </div>
            {editing ? (
              <textarea
                aria-label={`Lập luận ${explanationLabel(item)}`}
                {...register(`items.${index}.reasoning`)}
                rows={4}
                className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta"
              />
            ) : (
              <p className="whitespace-pre-wrap text-sm text-gray-600">{item.reasoning}</p>
            )}
          </div>
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
              Giải thích
            </div>
            {editing ? (
              <textarea
                aria-label={`Giải thích ${explanationLabel(item)}`}
                {...register(`items.${index}.explanation_text`)}
                rows={4}
                className="w-full rounded border border-gray-300 p-3 text-sm outline-none focus:border-terracotta"
              />
            ) : (
              <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{item.explanation_text}</p>
            )}
          </div>
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
              <span className="text-sm font-semibold">Câu {question.position + 1}</span>
              <span
                className={`inline-block w-fit rounded-[3px] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${
                  !passed
                    ? 'bg-red-100 text-red-800'
                    : selfChecked
                      ? 'bg-green-100 text-green-800'
                      : 'bg-gray-100 text-gray-700'
                }`}
              >
                {!passed ? 'Không đạt CRAG check' : selfChecked ? 'Đạt CRAG groundedness/answerability' : 'Chưa tự kiểm'}
              </span>
            </div>
            <QuestionEditor moduleId={moduleId} question={question}
              explanation={explanations[question.position] || question.explanation || ''} onSaved={onSaved} />
          </div>
        )
      })}
      {questions.length === 0 && (
        <p className="text-sm text-gray-500">
          Không sinh được câu hỏi hợp lệ
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
      {errors.stem && <p className="text-xs text-red-600">{errors.stem.message}</p>}
      {question.choices.options.map((_, i) => <div key={i} className="flex flex-wrap items-center gap-2">
        <input type="radio" value={String(i)} aria-label={`Đáp án ${String.fromCharCode(65 + i)}`} {...register('answer_key')} />
        <input aria-label={`Phương án ${String.fromCharCode(65 + i)}`} {...register(`choices.${i}`)} className="min-w-0 flex-1 rounded border border-gray-300 p-2" />
        {errors.choices?.[i] && <p className="basis-full text-xs text-red-600">{errors.choices[i].message}</p>}
      </div>)}
      <label>Giải thích<textarea aria-label="Giải thích" {...register('explanation')} className="w-full rounded border border-gray-300 p-2" /></label>
      <div className="flex gap-3">
        <button type="submit" disabled={saving} className="text-sm font-semibold text-terracotta">{saving ? 'Đang lưu...' : 'Lưu'}</button>
        <button type="button" disabled={saving} onClick={() => setEditing(false)} className="text-sm text-gray-500">Hủy</button>
      </div>
    </form> : <>
      <p className="font-medium">{question.stem}</p>
      <ul className="flex flex-col gap-1 text-sm">{question.choices.options.map((choice, i) => <li key={i} className={String(i) === question.answer_key ? 'font-semibold text-green-700' : 'text-gray-700'}>{String.fromCharCode(65 + i)}. {choice}{String(i) === question.answer_key ? ' ✓' : ''}</li>)}</ul>
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
