import axios, { type InternalAxiosRequestConfig } from 'axios'

export const api = axios.create({ baseURL: '/api' })

interface StoredAuth {
  token: string | null
  refreshToken: string | null
}

function readStoredAuth(): StoredAuth {
  const raw = localStorage.getItem('auth')
  if (!raw) return { token: null, refreshToken: null }
  const parsed = JSON.parse(raw) as { token: string | null; refreshToken?: string | null }
  return { token: parsed.token ?? null, refreshToken: parsed.refreshToken ?? null }
}

function writeStoredAuth(patch: Partial<StoredAuth>) {
  const raw = localStorage.getItem('auth')
  const current = raw ? JSON.parse(raw) : {}
  localStorage.setItem('auth', JSON.stringify({ ...current, ...patch }))
}

api.interceptors.request.use((config) => {
  const { token } = readStoredAuth()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// access token sống 1h (trước là 7 ngày) -- 1 request 401 vì hết hạn giữa
// phiên làm việc giờ là chuyện BÌNH THƯỜNG, không phải lỗi auth thật. Tự
// refresh 1 lần rồi phát lại đúng request gốc thay vì đá thẳng người dùng
// về /login. Gộp các lần refresh chồng nhau (nhiều request 401 cùng lúc)
// thành 1 lệnh gọi /auth/refresh duy nhất qua refreshPromise dùng chung.
let refreshPromise: Promise<string | null | undefined> | null = null

// Wrapped in an object so a test can watch it: assigning window.location is not
// something a test can observe.
export const navigation = {
  toLogin() {
    window.location.href = '/login'
  },
}

// A refresh token works once. A second tab presenting the same one a moment after the
// first is refused by the server without ending the session, and the tab that won has
// stored its new tokens by then or within moments, so wait that long and look there
// before deciding the session is over.
const OTHER_TAB_GRACE_MS = 300
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// A token: renewed. null: the session is over. undefined: could not tell (the server
// did not answer or is failing), which must not sign anybody out.
async function refreshAccessToken(): Promise<string | null | undefined> {
  const { refreshToken } = readStoredAuth()
  if (!refreshToken) return null
  try {
    const { data } = await axios.post<{ access_token: string; refresh_token: string }>(
      '/api/auth/refresh',
      { refresh_token: refreshToken },
    )
    writeStoredAuth({ token: data.access_token, refreshToken: data.refresh_token })
    return data.access_token
  } catch (error) {
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (status === undefined || status >= 500) return undefined
    await sleep(OTHER_TAB_GRACE_MS)
    const latest = readStoredAuth()
    return latest.refreshToken && latest.refreshToken !== refreshToken ? latest.token : null
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
    const isAuthEndpoint = config?.url?.startsWith('/auth/')

    if (error.response?.status === 401 && config && !config._retried && !isAuthEndpoint) {
      config._retried = true
      refreshPromise ??= refreshAccessToken().finally(() => {
        refreshPromise = null
      })
      const newAccessToken = await refreshPromise
      if (newAccessToken) {
        config.headers.Authorization = `Bearer ${newAccessToken}`
        return api(config)
      }
      // Could not reach the server: leave the session alone and let the caller report it.
      if (newAccessToken === undefined) return Promise.reject(error)
    }

    if (error.response?.status === 401 && window.location.pathname !== '/login') {
      localStorage.removeItem('auth')
      navigation.toLogin()
    }
    return Promise.reject(error)
  },
)

// The words to show a person for a failed request. The server answers with a message
// (`detail`) for the failures it explains, a list of them for a rejected body, and
// nothing useful for a network error or a rate limit, hence the fallback.
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (!axios.isAxiosError(error)) return fallback
  const status = error.response?.status
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
  // The message the server wrote for a refusal it explains (a duplicate email, a weak
  // password). The rate limiter's own 429 carries none, so it gets the general wording.
  if (typeof detail === 'string' && detail && status !== 413) return detail
  if (status === 429) return 'Bạn thao tác quá nhanh, vui lòng đợi một chút rồi thử lại.'
  if (status === 413) return 'Dữ liệu gửi lên quá lớn.'
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item?.msg === 'string' ? item.msg.replace(/^Value error, /, '') : ''))
      .filter(Boolean)
    if (messages.length > 0) return messages.join('. ')
  }
  return fallback
}

export interface UserInfo {
  id: string
  email: string
  name: string
  role: string
}

export interface Course {
  id: string
  name: string
  created_at: string
  moodle_course_id: number | null
}

export interface MoodleCourseSearchResult {
  id: number
  fullname: string
  shortname: string
}

export interface MoodleSection {
  sectionnum: number
  name: string
}

export type DocumentKind = 'lecture' | 'textbook'

export interface DocumentInfo {
  id: string
  filename: string
  status: string
  created_at: string
  module_count: number
  // A module is being generated from this document (queued or running job).
  generating: boolean
  kind: DocumentKind
  // Chapters and sections detected in a textbook; 0 means the whole document is used.
  section_count: number
}

export interface SectionInfo {
  id: string
  parent_id: string | null
  title: string
  level: number
  page_start: number | null
  page_end: number | null
  module_count: number
  generating: boolean
}

export interface ModuleSummary {
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
  stem: string
  choices: { options: string[] }
  answer_key: string
  explanation: string | null
}

export interface ModuleComponentInfo {
  id: string
  type: 'content' | 'slides' | 'example' | 'quiz' | 'practice' | 'explanation'
  content: Record<string, unknown>
  questions: QuestionInfo[]
}

export interface ModuleDetail {
  id: string
  title: string
  learning_objective: string | null
  bloom_level: string | null
  course_id: string
  moodle_course_id: number | null
  components: ModuleComponentInfo[]
}

export async function login(email: string, password: string) {
  const { data } = await api.post<{
    access_token: string
    refresh_token: string
    token_type: string
    user: UserInfo
  }>('/auth/login', { email, password })
  return data
}

export async function register(name: string, email: string, password: string) {
  const { data } = await api.post<UserInfo>('/auth/register', { name, email, password })
  return data
}

export async function logout(refreshToken: string) {
  // Best-effort -- nếu API lỗi thì vẫn xóa auth local ở AuthContext.logout(),
  // không chặn người dùng đăng xuất được vì 1 request mạng thất bại.
  await api.post('/auth/logout', { refresh_token: refreshToken }).catch(() => {})
}

export async function listCourses() {
  const { data } = await api.get<Course[]>('/courses')
  return data
}

export async function createCourse(name: string) {
  const { data } = await api.post<Course>('/courses', { name })
  return data
}

export async function deleteCourse(courseId: string) {
  await api.delete(`/courses/${courseId}`)
}

export async function searchMoodleCourses(q: string) {
  const { data } = await api.get<MoodleCourseSearchResult[]>('/courses/moodle/search', { params: { q } })
  return data
}

export async function linkMoodleCourse(courseId: string, moodleCourseId: number) {
  const { data } = await api.post<{ moodle_course_id: number }>(`/courses/${courseId}/link-moodle`, {
    moodle_course_id: moodleCourseId,
  })
  return data
}

export async function createMoodleCourseFor(courseId: string) {
  const { data } = await api.post<{ moodle_course_id: number }>(`/courses/${courseId}/create-moodle`)
  return data
}

export async function unlinkMoodleCourse(courseId: string) {
  await api.delete(`/courses/${courseId}/link-moodle`)
}

export async function getMoodleSections(courseId: string) {
  const { data } = await api.get<MoodleSection[]>(`/courses/${courseId}/moodle-sections`)
  return data
}

export async function listDocuments(courseId: string) {
  const { data } = await api.get<DocumentInfo[]>('/documents', { params: { course_id: courseId } })
  return data
}

export async function deleteDocument(documentId: string) {
  await api.delete(`/documents/${documentId}`)
}

// Upload/generate giờ chạy nền qua job queue (document_pipeline/
// module_generation, backend/app/services/job_queue.py) -- request trả về
// gần như ngay lập tức với job_id thay vì đợi toàn bộ xử lý xong (đã đo
// thật: 1 lần sinh module mất 10+ phút). Caller phải tự poll trạng thái
// job qua getDocumentJobStatus()/getModuleJobStatus() (xem pollJobStatus()).
export interface JobStatusInfo {
  id: string
  step: string
  status: 'queued' | 'running' | 'done' | 'failed'
  attempts: number
  last_error: string | null
}

export async function uploadDocument(courseId: string, file: File, kind: DocumentKind = 'lecture') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/documents', form, { params: { course_id: courseId, kind } })
  return data as { id: string; status: string; job_id: string }
}

export async function listModules(documentId: string) {
  const { data } = await api.get<ModuleSummary[]>('/modules', { params: { document_id: documentId } })
  return data
}

export async function generateModule(documentId: string) {
  const { data } = await api.post('/modules/generate', null, { params: { document_id: documentId } })
  return data as { job_id: string; status: string }
}

export async function listSections(documentId: string) {
  const { data } = await api.get<SectionInfo[]>(`/documents/${documentId}/sections`)
  return data
}

export async function generateModulesFromSections(documentId: string, sectionIds: string[]) {
  const { data } = await api.post(
    '/modules/generate',
    { section_ids: sectionIds },
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

export interface PublishToMoodleResult {
  moodle_course_id: number
  book_cmid: number
  practice_cmid?: number
  quiz_cmid?: number
}

export async function publishToMoodle(moduleId: string, sectionNum: number) {
  const { data } = await api.post<PublishToMoodleResult>(`/modules/${moduleId}/publish-to-moodle`, {
    section_num: sectionNum,
  })
  return data
}

export interface FailedJobInfo {
  id: string
  job_type: 'document_pipeline' | 'module_generation'
  step: string
  attempts: number
  max_attempts: number
  last_error: string | null
  created_at: string
  document_filename: string
  course_name: string
  module_id: string | null
}

export async function listFailedJobs(jobType?: FailedJobInfo['job_type']) {
  const { data } = await api.get<FailedJobInfo[]>('/jobs/failed', {
    params: jobType ? { job_type: jobType } : undefined,
  })
  return data
}
