import axios, { type AxiosProgressEvent } from 'axios'
import { api } from './client'

// Where the server said to send a file: a presigned S3 form POST, or, without S3, a PUT to the API.
export interface UploadTarget {
  method: 'POST' | 'PUT'
  url: string
  fields: Record<string, string>
}

export async function sendFile(upload: UploadTarget, file: File, onProgress?: (fraction: number) => void) {
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
}

// Where the server said to get a file: a short-lived S3 link the browser follows itself, or,
// without S3, an API path that needs the token, so the file comes down as a blob.
export interface DownloadTarget {
  url: string
  via_api: boolean
  filename: string
}

export async function followDownload(target: DownloadTarget) {
  const link = document.createElement('a')
  link.download = target.filename
  if (!target.via_api) {
    link.href = target.url
    link.click()
    return
  }
  const { data } = await api.get<Blob>(target.url, { responseType: 'blob' })
  const url = URL.createObjectURL(data)
  link.href = url
  link.click()
  URL.revokeObjectURL(url)
}
