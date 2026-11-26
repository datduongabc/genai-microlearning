import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as adminApi from '../../../api/admin'
import { api } from '../../../api/client'
import * as flashcardsApi from '../../../api/flashcards'
import { retryJob } from '../../../api/jobs'
import * as learnApi from '../../../api/learn'
import { listModels, setDefaultModel } from '../../../api/models'
import { deleteModule, generateModule } from '../../../api/modules'
import { linkMoodleCourse, MOODLE_ACCOUNT_CHANGED_EVENT, publishToMoodle } from '../../../api/moodle'
import * as studiesApi from '../../../api/studies'
import * as videosApi from '../../../api/videos'

function failure(status: number) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  const response = { status, statusText: '', data: { detail: 'x' }, headers: {}, config } as AxiosResponse
  return new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response)
}

function listen() {
  const heard = vi.fn()
  window.addEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, heard)
  return { heard, stop: () => window.removeEventListener(MOODLE_ACCOUNT_CHANGED_EVENT, heard) }
}

describe('API wrappers and endpoints', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
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

  it('sends the selected output language for whole documents', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
    await generateModule('doc', null, 'en')
    expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'en' }, { params: { document_id: 'doc' } })
    await generateModule('doc')
    expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'vi' }, { params: { document_id: 'doc' } })
  })

  it('sends trimmed guidance with the selected language for documents', async () => {
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
    await generateModule('doc', null, 'en', '  Explain IDS  ')
    expect(post).toHaveBeenLastCalledWith('/modules/generate', { output_language: 'en', custom_instructions: 'Explain IDS' }, { params: { document_id: 'doc' } })
  })

  it('deletes a module by id', async () => {
    const del = vi.spyOn(api, 'delete').mockResolvedValue({ data: { status: 'deleted', deleted_module_ids: ['m'] } })
    await deleteModule('m')
    expect(del).toHaveBeenCalledWith('/modules/m')
  })

  it.each([
    ['publishing', () => publishToMoodle('m', 0)],
    ['linking a course', () => linkMoodleCourse('c', 6)],
  ])('a 422 answer to %s says the Moodle account may have changed, and the error still reaches the caller', async (_name, send) => {
    vi.spyOn(api, 'post').mockRejectedValue(failure(422))
    const { heard, stop } = listen()

    await expect(send()).rejects.toBeInstanceOf(AxiosError)

    expect(heard).toHaveBeenCalledOnce()
    stop()
  })

  it('other failures and successes of Moodle calls leave the account alone', async () => {
    const { heard, stop } = listen()
    vi.spyOn(api, 'post').mockRejectedValueOnce(failure(502)).mockResolvedValueOnce({ data: { moodle_course_id: 6 } })

    await expect(publishToMoodle('m', 0)).rejects.toBeInstanceOf(AxiosError)
    await linkMoodleCourse('c', 6)

    expect(heard).not.toHaveBeenCalled()
    stop()
  })
})
