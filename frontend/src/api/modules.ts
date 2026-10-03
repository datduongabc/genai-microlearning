import { api } from './client'

export interface ModuleSummary {
  is_control: boolean
  id: string
  title: string
  learning_objective: string | null
  bloom_level: string | null
  component_count: number
  // The textbook section the module was generated from; null for a whole document.
  section_id: string | null
}

export interface QuestionInfo {
  id: string
  // Order inside its quiz or practice; explanations are matched to questions by it.
  position: number
  stem: string
  choices: { options: string[] }
  answer_key: string
  explanation: string | null
}

export interface ExampleItem {
  context: string
  analysis: string
}

export interface ExplanationItem {
  component: 'quiz' | 'practice'
  position: number
  reasoning: string
  explanation_text: string
}

export interface QuestionCheck {
  target?: string
  passed?: boolean
  fail_reason?: string | null
}

export interface ModuleComponentInfo {
  id: string
  type: 'content' | 'slides' | 'example' | 'quiz' | 'practice' | 'explanation'
  content: Record<string, unknown>
  questions: QuestionInfo[]
}

export interface ModuleDetail {
  is_control: boolean
  id: string
  title: string
  learning_objective: string | null
  bloom_level: string | null
  sub_objectives?: string[] | null
  course_id: string
  moodle_course_id: number | null
  components: ModuleComponentInfo[]
  chat_edit_count?: number
  claim_citations?: {
    claim: string
    locator: string
    chunk_content: string | null
    resolution: 'resolved' | 'missing' | 'ambiguous'
  }[] | null
}

export interface JobStatusInfo {
  id: string
  step: string
  status: 'queued' | 'running' | 'done' | 'failed'
  attempts: number
  last_error: string | null
}

export async function listModules(documentId: string) {
  const { data } = await api.get<ModuleSummary[]>('/modules', { params: { document_id: documentId } })
  return data
}

// modelAlias bỏ trống thì KHÔNG gửi khóa model_alias, để backend tự lấy mặc định của giảng
// viên rồi tới cấu hình hệ thống. Gửi null tường minh cũng được, nhưng bỏ hẳn giữ đúng
// nghĩa "chưa chọn gì" và không phụ thuộc vào việc backend coi null với vắng mặt là một.
function modelBody(modelAlias?: string | null) {
  return modelAlias ? { model_alias: modelAlias } : {}
}

export async function generateModule(documentId: string, modelAlias?: string | null, outputLanguage: "vi" | "en" = "vi", customInstructions = "") {
  const { data } = await api.post('/modules/generate', { ...modelBody(modelAlias), output_language: outputLanguage, ...(customInstructions.trim() ? { custom_instructions: customInstructions.trim() } : {}) }, {
    params: { document_id: documentId },
  })
  return data as { job_id: string; status: string }
}

export async function generateModulesFromSections(
  documentId: string,
  sectionIds: string[],
  modelAlias?: string | null,
  outputLanguage: "vi" | "en" = "vi",
  customInstructions = "",
) {
  const { data } = await api.post(
    '/modules/generate',
    { section_ids: sectionIds, ...modelBody(modelAlias), output_language: outputLanguage, ...(customInstructions.trim() ? { custom_instructions: customInstructions.trim() } : {}) },
    { params: { document_id: documentId } },
  )
  return data as { jobs: { section_id: string; job_id: string }[] }
}

export async function getModuleJobStatus(jobId: string) {
  const { data } = await api.get(`/modules/jobs/${jobId}`)
  return data as JobStatusInfo & { module_id: string | null }
}

// Poll đơn giản (không backoff, đủ cho 1 người dùng/phiên) -- dừng khi job
// vào 1 trong 2 trạng thái cuối (done/failed) hoặc hết timeoutMs.
export async function pollJobStatus<T extends JobStatusInfo>(
  getStatus: () => Promise<T>,
  { intervalMs = 3000, timeoutMs = 20 * 60 * 1000 } = {},
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const status = await getStatus()
    if (status.status === 'done' || status.status === 'failed') return status
    if (Date.now() >= deadline) return status
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}

export async function getModule(moduleId: string) {
  const { data } = await api.get<ModuleDetail>(`/modules/${moduleId}`)
  return data
}

// Cũng xóa bản đối chứng không RAG đi cặp (nếu có); Book/Quiz đã đẩy lên Moodle thì giữ nguyên.
export async function deleteModule(moduleId: string) {
  const { data } = await api.delete<{ status: string; deleted_module_ids: string[] }>(`/modules/${moduleId}`)
  return data
}

export async function updateModuleTitle(moduleId: string, title: string) {
  const { data } = await api.patch(`/modules/${moduleId}`, null, { params: { title } })
  return data
}

export async function updateComponentContent(
  moduleId: string,
  componentId: string,
  content: Record<string, unknown>,
) {
  const { data } = await api.patch(`/modules/${moduleId}/components/${componentId}`, { content })
  return data as { id: string; content: Record<string, unknown> }
}

export async function updateQuestion(
  moduleId: string,
  questionId: string,
  question: { stem: string; choices: string[]; answer_key: number; explanation: string },
) {
  const { data } = await api.patch(`/modules/${moduleId}/questions/${questionId}`, question)
  return data as Omit<QuestionInfo, 'position'>
}

export async function chatEditModule(moduleId: string, message: string, sessionId?: string) {
  const { data } = await api.post<{ session_id: string; reply: string; applied: boolean; edit_count: number }>(
    `/modules/${moduleId}/chat-edit`, { message, session_id: sessionId },
  )
  return data
}
