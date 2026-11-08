import axios, { type AxiosProgressEvent } from 'axios'
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

// Where step 2 sends the file: a presigned S3 POST on AWS (url + form fields), or the API itself
// with the local store (PUT, a path under the API's base URL).
interface UploadTarget {
  method: 'POST' | 'PUT'
  url: string
  fields: Record<string, string>
}

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
  const { upload } = started
  const reportProgress = (event: AxiosProgressEvent) => {
    if (onProgress && event.total) onProgress(event.loaded / event.total)
  }

  if (upload.method === 'POST') {
    // A plain request to S3: no API base URL, and no Authorization header, which S3 would reject.
    const form = new FormData()
    for (const [name, value] of Object.entries(upload.fields)) form.append(name, value)
    form.append('file', file) // S3 ignores every field after the file
    await axios.post(upload.url, form, { onUploadProgress: reportProgress })
  } else {
    await api.put(upload.url, file, {
      headers: { 'Content-Type': 'application/octet-stream' },
      onUploadProgress: reportProgress,
    })
  }

  const { data } = await api.post(`/documents/${started.document_id}/complete`)
  return data as { id: string; status: string; job_id: string }
}
