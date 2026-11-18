import { useContext, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import Markdown, { type Components } from 'react-markdown'
import { modelMarkdownProps } from '../lib/markdownMath'
import { COMPONENT_LABEL, SAVE_FAILED, SourceContext, type Citation } from '../lib/moduleParts'
import {
  examplesContentSchema,
  explanationsContentSchema,
  markdownContentSchema,
  questionEditSchema,
  slidesContentSchema,
  type ExamplesContentFormValues,
  type ExplanationsContentFormValues,
  type MarkdownContentFormValues,
  type QuestionEditFormValues,
  type QuestionEditInput,
  type SlidesContentFormValues,
} from '../lib/formSchemas'
import {
  api,
  apiErrorMessage,
  blobErrorMessage,
  updateComponentContent,
  updateQuestion,
  type ExampleItem,
  type ExplanationItem,
  type ModuleComponentInfo,
  type ModuleDetail,
  type QuestionCheck,
  type QuestionInfo,
} from '../api'

export function ComponentCard({
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
    <section id={`part-${component.type}`} className="module-part" data-edit-scope>
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
export function ScormDownload({ moduleId, moduleTitle }: { moduleId: string; moduleTitle: string }) {
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
      <button onClick={() => { setError(''); onEdit() }} data-edit-trigger className="ui-button self-start">
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
  // One level under the part's own h2, so a screen reader's list of headings has no gap; the look
  // stays as it was.
  h1: ({ children }) => <h3 className="mb-2 mt-4 text-base font-semibold tracking-normal first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mb-2 mt-4 text-base font-semibold tracking-normal first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mb-1 mt-3 font-semibold first:mt-0">{children}</h4>,
  h4: ({ children }) => <h5 className="mb-1 mt-3 font-semibold first:mt-0">{children}</h5>,
  h5: ({ children }) => <h6 className="mb-1 mt-3 font-semibold first:mt-0">{children}</h6>,
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
  return <div className="flex flex-col gap-3" data-edit-scope>
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
