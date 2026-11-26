import axios from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as adminApi from '../../../api/admin'
import { api, apiErrorMessage, blobErrorMessage, fetchWithAuth, navigation } from '../../../api/client'
import * as flashcardsApi from '../../../api/flashcards'
import { retryJob } from '../../../api/jobs'
import * as learnApi from '../../../api/learn'
import { listModels, setDefaultModel } from '../../../api/models'
import * as studiesApi from '../../../api/studies'
import { followDownload, sendFile } from '../../../api/upload'
import * as videosApi from '../../../api/videos'

describe('API wrappers and upload/auth helpers', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sendFile sends S3 FormData with file last on POST and octet-stream on PUT with progress', async () => {
    const postSpy = vi.spyOn(axios, 'post').mockResolvedValue({ data: {} })
    const putSpy = vi.spyOn(api, 'put').mockResolvedValue({ data: {} })
    const progress = vi.fn()
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })

    await sendFile({ method: 'POST', url: 'https://s3.example/bucket', fields: { key: 'k/1', policy: 'p' } }, file, progress)
    expect(postSpy).toHaveBeenCalledOnce()
    const [postUrl, form, postConfig] = postSpy.mock.calls[0]
    expect(postUrl).toBe('https://s3.example/bucket')
    expect(Array.from((form as FormData).keys())).toEqual(['key', 'policy', 'file'])
    postConfig?.onUploadProgress?.({ loaded: 5, total: 10 } as never)
    expect(progress).toHaveBeenCalledWith(0.5)

    await sendFile({ method: 'PUT', url: '/documents/1/blob', fields: {} }, file, progress)
    expect(putSpy).toHaveBeenCalledWith('/documents/1/blob', file, expect.objectContaining({
      headers: { 'Content-Type': 'application/octet-stream' },
    }))
    putSpy.mock.calls[0][2]?.onUploadProgress?.({ loaded: 3, total: 3 } as never)
    expect(progress).toHaveBeenCalledWith(1)
  })

  it('followDownload uses direct URL when via_api is false and fetches/revokes blob when true', async () => {
    const clicks: Array<{ href: string; download: string }> = []
    const origCreate = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      const el = origCreate(tag)
      if (tag === 'a') {
        vi.spyOn(el as HTMLAnchorElement, 'click').mockImplementation(() => {
          clicks.push({ href: (el as HTMLAnchorElement).href, download: (el as HTMLAnchorElement).download })
        })
      }
      return el
    })

    await followDownload({ url: 'https://s3.example/file.pdf', via_api: false, filename: 'direct.pdf' })
    expect(clicks).toEqual([{ href: 'https://s3.example/file.pdf', download: 'direct.pdf' }])

    const blob = new Blob(['data'])
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: blob })
    const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:local/42')
    const revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})

    await followDownload({ url: '/studies/1/files/2/blob', via_api: true, filename: 'ref.pdf' })
    expect(getSpy).toHaveBeenCalledWith('/studies/1/files/2/blob', { responseType: 'blob' })
    expect(createUrl).toHaveBeenCalledWith(blob)
    expect(revokeUrl).toHaveBeenCalledWith('blob:local/42')
    expect(clicks[1]).toEqual({ href: 'blob:local/42', download: 'ref.pdf' })
  })

  it('fetchWithAuth refreshes once on 401 and signs out when no refresh token exists', async () => {
    const navSpy = vi.spyOn(navigation, 'toLogin').mockImplementation(() => {})
    localStorage.setItem('auth', JSON.stringify({ token: 'expired', refreshToken: 'rt-1' }))
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response('ok', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(axios, 'post').mockResolvedValueOnce({ data: { access_token: 'fresh', refresh_token: 'rt-2' } })

    const res = await fetchWithAuth('/courses/c1/chat', { method: 'POST', headers: { 'X-Test': '1' } })
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/courses/c1/chat', expect.objectContaining({
      headers: { 'X-Test': '1', Authorization: 'Bearer fresh' },
    }))
    expect(navSpy).not.toHaveBeenCalled()

    localStorage.setItem('auth', JSON.stringify({ token: 'expired', refreshToken: null }))
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }))
    const rejected = await fetchWithAuth('/courses/c1/chat', { method: 'POST' })
    expect(rejected.status).toBe(401)
    expect(navSpy).toHaveBeenCalledOnce()
  })

  it('apiErrorMessage and blobErrorMessage format status codes, validation lists, and blobs', async () => {
    const mkAxiosErr = (status: number, data: unknown) => {
      const err = new axios.AxiosError('fail', 'ERR_BAD_REQUEST', undefined, undefined, {
        status,
        statusText: 'Err',
        headers: {},
        config: {} as never,
        data,
      })
      return err
    }

    expect(apiErrorMessage(new Error('other'), 'fallback')).toBe('fallback')
    expect(apiErrorMessage(mkAxiosErr(429, {}), 'fallback')).toContain('quá nhanh')
    expect(apiErrorMessage(mkAxiosErr(413, { detail: 'ignored' }), 'fallback')).toBe('Dữ liệu gửi lên quá lớn.')
    expect(apiErrorMessage(mkAxiosErr(422, { detail: [{ msg: 'Value error, quá ngắn' }] }), 'fallback')).toBe('quá ngắn')

    const blobErr = mkAxiosErr(400, new Blob([JSON.stringify({ detail: 'Sai định dạng' })]))
    expect(await blobErrorMessage(blobErr, 'fallback')).toBe('Sai định dạng')
    const htmlBlobErr = mkAxiosErr(502, new Blob(['<html>bad gateway</html>']))
    expect(await blobErrorMessage(htmlBlobErr, 'fallback')).toBe('fallback')
  })

  it('dispatches admin, studies, flashcards, videos, models, jobs, and learn requests', async () => {
    const getSpy = vi.spyOn(api, 'get').mockResolvedValue({ data: { items: [], emails: [], upload: { method: 'PUT', url: '/u', fields: {} } }, status: 200 })
    const postSpy = vi.spyOn(api, 'post').mockResolvedValue({ data: { retried: [], skipped: [], reasons: {}, file_id: 'f1', upload: { method: 'PUT', url: '/u', fields: {} } }, status: 201 })
    const putSpy = vi.spyOn(api, 'put').mockResolvedValue({ data: {} })
    const delSpy = vi.spyOn(api, 'delete').mockResolvedValue({ data: {} })

    await adminApi.getAdminCounts()
    await adminApi.listUsers(false)
    await adminApi.setUserActive('u1', true)
    await adminApi.listFailedJobs(['video_render', 'module_generation'])
    await adminApi.retryFailedJobs(['j1'])
    await adminApi.createUser('An', 'an@example.com', 'pw')
    await adminApi.resetUserPassword('u1', 'pw2')
    await adminApi.unlinkUserMoodleAccount('u1')
    await adminApi.eraseUser('u1')
    await adminApi.listUserCourses('u1')
    await adminApi.transferCourse('c1', 'u2', true)

    await flashcardsApi.generateFlashcards('d1', { focus: '  Heap  ', outputLanguage: 'vi', modelAlias: 'fast' })
    await flashcardsApi.listFlashcardSets('c1')
    await flashcardsApi.getFlashcardSet('fs1')
    await flashcardsApi.saveFlashcards('fs1', 2, [])
    await flashcardsApi.reviseFlashcardSet('fs1', 2, 'rút gọn')
    await flashcardsApi.publishFlashcardSet('fs1', false)
    await flashcardsApi.deleteFlashcardSet('fs1')

    await videosApi.getMediaModels()
    await videosApi.generateVideo('d1', { focus: ' ', outputLanguage: 'en', voiceAlias: 'v1', imageAlias: 'img1' })
    await videosApi.listVideos('c1')
    await videosApi.getVideo('v1')
    await videosApi.saveVideoScenes('v1', 1, [])
    await videosApi.reviseVideo('v1', 1, 'thêm ví dụ')
    await videosApi.drawIllustrations('v1', [0, 2])
    await videosApi.renderVideo('v1')
    await videosApi.publishVideo('v1', true)
    await videosApi.deleteVideo('v1')
    await videosApi.getVideoFile('v1')
    await videosApi.videoFileBlob('/videos/v1/blob')
    await videosApi.getScenePreview('v1', 1, 'v2')

    expect(await studiesApi.addPair('s1', 'm1')).toBe('added')
    postSpy.mockResolvedValueOnce({ data: {}, status: 202 })
    expect(await studiesApi.addPair('s1', 'm2')).toBe('generating')
    await studiesApi.listPairCandidates('s1')
    await studiesApi.setRaters('s1', ['a@b.com'], { 'a@b.com': ['p1'] })
    await studiesApi.myStudies()
    await studiesApi.getStudyResults('s1')
    await studiesApi.downloadStudyFile('s1', 'items.csv')
    await studiesApi.getStudyItems('s1')
    await studiesApi.removeStudyItem('s1', 'i1')
    await studiesApi.listStudyFiles('s1')
    await studiesApi.deleteStudyFile('s1', 'f1')
    await studiesApi.setItemReference('s1', 'i1', 'f1')
    await studiesApi.uploadStudyFile('s1', new File(['x'], 'ref.pdf'))

    await retryJob('j9')
    await listModels()
    await setDefaultModel('fast')
    await learnApi.listLearnItems('c1')
    await learnApi.getLearnFlashcardSet('fs1')
    await learnApi.getLearnVideo('v1')

    expect(getSpy).toHaveBeenCalledWith('/admin/jobs/failed', { params: expect.any(URLSearchParams) })
    expect(postSpy).toHaveBeenCalledWith('/flashcard-sets', { output_language: 'vi', focus: 'Heap', model_alias: 'fast' }, { params: { document_id: 'd1' } })
    expect(postSpy).toHaveBeenCalledWith('/videos', { output_language: 'en', voice_alias: 'v1', image_alias: 'img1' }, { params: { document_id: 'd1' } })
    expect(putSpy).toHaveBeenCalledWith('/models/default', { alias: 'fast' })
    expect(delSpy).toHaveBeenCalledWith('/videos/v1')
  })
})
