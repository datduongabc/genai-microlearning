import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../../../api/client'
import { followDownload } from '../../../api/upload'

afterEach(() => vi.restoreAllMocks())

it('follows an S3 link itself, so the token never goes to S3', async () => {
  const get = vi.spyOn(api, 'get')
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.href).toBe('https://bucket.s3.amazonaws.com/study-files/f1.pdf?X-Amz-Signature=x')
    expect(this.download).toBe('Bài giảng.pdf')
  })
  await followDownload({ url: 'https://bucket.s3.amazonaws.com/study-files/f1.pdf?X-Amz-Signature=x', via_api: false, filename: 'Bài giảng.pdf' })
  expect(click).toHaveBeenCalledOnce()
  expect(get).not.toHaveBeenCalled()
})

it('fetches an API path with the token and saves it as a blob', async () => {
  const blob = new Blob(['%PDF'])
  const get = vi.spyOn(api, 'get').mockResolvedValue({ data: blob })
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:ref'), revokeObjectURL: vi.fn() })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  try {
    await followDownload({ url: '/studies/mine/s1/items/i1/reference/content', via_api: true, filename: 'a.pdf' })
    expect(get).toHaveBeenCalledWith('/studies/mine/s1/items/i1/reference/content', { responseType: 'blob' })
    expect(click).toHaveBeenCalledOnce()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:ref')
  } finally {
    vi.unstubAllGlobals()
  }
})
