import axios from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { uploadDocument } from '../../../api/documents'
import { followDownload, sendFile } from '../../../api/upload'

const file = new File(['abc'], 'lecture.mp4', { type: 'video/mp4' })
const done = { data: { id: 'doc-1', status: 'uploaded', job_id: 'job-1' } }

describe('upload and download helpers', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('sendFile sends S3 FormData with file last on POST and octet-stream on PUT with progress', async () => {
    const postSpy = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    const putSpy = vi.spyOn(api, 'put').mockResolvedValue({ data: {} })
    const progress = vi.fn()
    const notesFile = new File(['hello'], 'notes.txt', { type: 'text/plain' })

    await sendFile({ method: 'POST', url: 'https://s3.example/bucket', fields: { key: 'k/1', policy: 'p' } }, notesFile, progress)
    expect(postSpy).toHaveBeenCalledOnce()
    const [postUrl, form, postConfig] = postSpy.mock.calls[0]
    expect(postUrl).toBe('https://s3.example/bucket')
    expect(Array.from((form as FormData).keys())).toEqual(['key', 'policy', 'file'])
    postConfig?.onUploadProgress?.({ loaded: 5, total: 10 } as never)
    expect(progress).toHaveBeenCalledWith(0.5)

    await sendFile({ method: 'PUT', url: '/documents/1/blob', fields: {} }, notesFile, progress)
    expect(putSpy).toHaveBeenCalledWith('/documents/1/blob', notesFile, expect.objectContaining({
      headers: { 'Content-Type': 'application/octet-stream' },
    }))
    putSpy.mock.calls[0][2]?.onUploadProgress?.({ loaded: 3, total: 3 } as never)
    expect(progress).toHaveBeenCalledWith(1)
  })

  it('uploadDocument posts the file straight to S3 with the presigned fields, then reports it sent', async () => {
    const apiPostSpy = vi.spyOn(api, 'post')
      .mockResolvedValueOnce({
        data: {
          document_id: 'doc-1',
          upload: { method: 'POST', url: 'https://b.s3.ap-southeast-1.amazonaws.com/', fields: { key: 'uploads/doc-1.mp4', policy: 'p' } },
        },
      })
      .mockResolvedValueOnce(done)
    const apiPutSpy = vi.spyOn(api, 'put')
    const axiosPostSpy = vi.spyOn(axios, 'post').mockImplementation(async (_url, _body, config) => {
      config?.onUploadProgress?.({ loaded: 1, total: 4 } as never)
      return { data: '' }
    })
    const progress = vi.fn()

    const result = await uploadDocument('course-1', file, progress)

    expect(apiPostSpy).toHaveBeenNthCalledWith(1, '/documents/uploads', { filename: 'lecture.mp4', size: 3 }, { params: { course_id: 'course-1' } })
    const [url, form] = axiosPostSpy.mock.calls[0]
    expect(url).toBe('https://b.s3.ap-southeast-1.amazonaws.com/')
    // S3 reads the policy fields first and ignores anything after the file.
    expect([...(form as FormData).keys()]).toEqual(['key', 'policy', 'file'])
    expect(apiPutSpy).not.toHaveBeenCalled()
    expect(progress).toHaveBeenCalledWith(0.25)
    expect(apiPostSpy).toHaveBeenNthCalledWith(2, '/documents/doc-1/complete')
    expect(result).toEqual(done.data)
  })

  it('uploadDocument puts the file to the API itself without onProgress when the server has no S3', async () => {
    const apiPostSpy = vi.spyOn(api, 'post')
      .mockResolvedValueOnce({ data: { document_id: 'doc-1', upload: { method: 'PUT', url: '/documents/doc-1/content', fields: {} } } })
      .mockResolvedValueOnce(done)
    const apiPutSpy = vi.spyOn(api, 'put').mockResolvedValue({ data: {} })
    const axiosPostSpy = vi.spyOn(axios, 'post')

    await uploadDocument('course-1', file)

    expect(apiPutSpy).toHaveBeenCalledWith('/documents/doc-1/content', file, expect.objectContaining({
      headers: { 'Content-Type': 'application/octet-stream' },
    }))
    expect(axiosPostSpy).not.toHaveBeenCalled()
    expect(apiPostSpy).toHaveBeenLastCalledWith('/documents/doc-1/complete')
  })

  it('uploadDocument does not report the upload sent when sending the file failed', async () => {
    const apiPostSpy = vi.spyOn(api, 'post').mockResolvedValueOnce({
      data: { document_id: 'doc-1', upload: { method: 'POST', url: 'https://s3/', fields: {} } },
    })
    vi.spyOn(axios, 'post').mockRejectedValue(new Error('Network Error'))

    await expect(uploadDocument('course-1', file)).rejects.toThrow('Network Error')
    expect(apiPostSpy).toHaveBeenCalledTimes(1)
  })

  it('followDownload follows an S3 link itself, so the token never goes to S3', async () => {
    const get = vi.spyOn(api, 'get')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.href).toBe('https://bucket.s3.amazonaws.com/study-files/f1.pdf?X-Amz-Signature=x')
      expect(this.download).toBe('Bài giảng.pdf')
    })

    await followDownload({ url: 'https://bucket.s3.amazonaws.com/study-files/f1.pdf?X-Amz-Signature=x', via_api: false, filename: 'Bài giảng.pdf' })

    expect(click).toHaveBeenCalledOnce()
    expect(get).not.toHaveBeenCalled()
  })

  it('followDownload fetches an API path with the token and saves it as a blob', async () => {
    const blob = new Blob(['%PDF'])
    const get = vi.spyOn(api, 'get').mockResolvedValue({ data: blob })
    const createUrl = vi.fn(() => 'blob:ref')
    const revokeUrl = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: createUrl, revokeObjectURL: revokeUrl })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await followDownload({ url: '/studies/mine/s1/items/i1/reference/content', via_api: true, filename: 'a.pdf' })

    expect(get).toHaveBeenCalledWith('/studies/mine/s1/items/i1/reference/content', { responseType: 'blob' })
    expect(createUrl).toHaveBeenCalledWith(blob)
    expect(click).toHaveBeenCalledOnce()
    expect(revokeUrl).toHaveBeenCalledWith('blob:ref')
  })
})
