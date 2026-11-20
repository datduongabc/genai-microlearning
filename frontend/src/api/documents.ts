import { api } from './client'
import { sendFile, type UploadTarget } from './upload'

// A failed job that still needs the lecturer: the document itself (document_pipeline), one
// module generation (module_generation), or a flashcard set or video script that failed to
// generate. A failed picture or render is not here: the video editor shows and retries it.
export interface DocumentFailure {
  job_id: string
  job_type: 'document_pipeline' | 'module_generation' | 'flashcard_generation' | 'video_script'
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

// Where step 2 sends the file: a presigned S3 POST on AWS (url + form fields), or the API itself
// with the local store (PUT, a path under the API's base URL).
// Three steps, so that the file goes straight to S3 instead of through an API task:
// 1. ask where to send it (the server checks the format and size and creates the document),
// 2. send it, reporting progress, 3. report it sent, which queues the background pipeline.
// The pipeline then runs as a job; callers follow it with listDocuments()/getDocumentJobStatus().
export async function uploadDocument(courseId: string, file: File, onProgress?: (fraction: number) => void) {
  const { data: started } = await api.post<{ document_id: string; upload: UploadTarget }>(
    '/documents/uploads',
    { filename: file.name, size: file.size },
    { params: { course_id: courseId } },
  )
  await sendFile(started.upload, file, onProgress)

  const { data } = await api.post(`/documents/${started.document_id}/complete`)
  return data as { id: string; status: string; job_id: string }
}
