import { api } from './client'

// A failed job that still needs the lecturer: the document itself (document_pipeline) or one
// module generation (module_generation).
export interface DocumentFailure {
  job_id: string
  job_type: 'document_pipeline' | 'module_generation'
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
  // Open failures stay here after a reload: a failed generation makes no module to show them on.
  failures: DocumentFailure[]
  extraction_warnings: { code: string; page: number }[]
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
export async function uploadDocument(courseId: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/documents', form, { params: { course_id: courseId } })
  return data as { id: string; status: string; job_id: string }
}
