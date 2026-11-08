import axios from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { uploadDocument } from '../../../api/documents'
import { api } from '../../../api/client'

vi.mock('axios', async (importOriginal) => {
  const actual = await importOriginal<typeof import('axios')>()
  return { ...actual, default: { ...actual.default, post: vi.fn() } }
})
vi.mock('../../../api/client', () => ({ api: { post: vi.fn(), put: vi.fn() } }))

const file = new File(['abc'], 'lecture.mp4', { type: 'video/mp4' })
const done = { data: { id: 'doc-1', status: 'uploaded', job_id: 'job-1' } }

describe('uploadDocument', () => {
  beforeEach(() => vi.clearAllMocks())

  it('posts the file straight to S3 with the presigned fields, then reports it sent', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        data: {
          document_id: 'doc-1',
          upload: { method: 'POST', url: 'https://b.s3.ap-southeast-1.amazonaws.com/', fields: { key: 'uploads/doc-1.mp4', policy: 'p' } },
        },
      })
      .mockResolvedValueOnce(done)
    vi.mocked(axios.post).mockImplementation(async (_url, _body, config) => {
      config?.onUploadProgress?.({ loaded: 1, total: 4 } as never)
      return { data: '' }
    })
    const progress = vi.fn()

    const result = await uploadDocument('course-1', file, progress)

    expect(api.post).toHaveBeenNthCalledWith(1, '/documents/uploads', { filename: 'lecture.mp4', size: 3 }, { params: { course_id: 'course-1' } })
    const [url, form] = vi.mocked(axios.post).mock.calls[0]
    expect(url).toBe('https://b.s3.ap-southeast-1.amazonaws.com/')
    // S3 reads the policy fields first and ignores anything after the file.
    expect([...(form as FormData).keys()]).toEqual(['key', 'policy', 'file'])
    expect(api.put).not.toHaveBeenCalled()
    expect(progress).toHaveBeenCalledWith(0.25)
    expect(api.post).toHaveBeenNthCalledWith(2, '/documents/doc-1/complete')
    expect(result).toEqual(done.data)
  })

  it('puts the file to the API itself when the server has no S3', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: { document_id: 'doc-1', upload: { method: 'PUT', url: '/documents/doc-1/content', fields: {} } } })
      .mockResolvedValueOnce(done)
    vi.mocked(api.put).mockResolvedValue({ data: {} })

    await uploadDocument('course-1', file)

    expect(api.put).toHaveBeenCalledWith('/documents/doc-1/content', file, expect.objectContaining({
      headers: { 'Content-Type': 'application/octet-stream' },
    }))
    expect(axios.post).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenLastCalledWith('/documents/doc-1/complete')
  })

  it('does not report the upload sent when sending the file failed', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({
      data: { document_id: 'doc-1', upload: { method: 'POST', url: 'https://s3/', fields: {} } },
    })
    vi.mocked(axios.post).mockRejectedValue(new Error('Network Error'))

    await expect(uploadDocument('course-1', file)).rejects.toThrow('Network Error')
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
