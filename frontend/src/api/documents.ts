import { api } from './client'

export type DocumentKind = 'lecture' | 'textbook'

// A failed job that still needs the lecturer: the document itself (document_pipeline) or one
// module generation (module_generation, for a section of a textbook when section_id is set).
export interface DocumentFailure {
  job_id: string
  job_type: 'document_pipeline' | 'module_generation'
  section_id: string | null
  section_title: string | null
  error: string
}

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
  // Open failures stay here after a reload: a failed generation makes no module to show them on.
  failures: DocumentFailure[]
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
export async function uploadDocument(courseId: string, file: File, kind: DocumentKind = 'lecture') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/documents', form, { params: { course_id: courseId, kind } })
  return data as { id: string; status: string; job_id: string }
}

export async function listSections(documentId: string) {
  const { data } = await api.get<SectionInfo[]>(`/documents/${documentId}/sections`)
  return data
}
