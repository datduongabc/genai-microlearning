import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './client'
import {
  createMoodleCourseFor,
  dismissMoodlePrompt,
  linkMoodleAccount,
  linkMoodleCourse,
  MOODLE_ACCOUNT_CHANGED_EVENT,
  publishToMoodle,
} from './moodle'

afterEach(() => vi.restoreAllMocks())

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

it.each([
  ['publishing', () => publishToMoodle('m', 0)],
  ['linking a course', () => linkMoodleCourse('c', 6)],
  ['creating a course', () => createMoodleCourseFor('c')],
])('a 422 answer to %s says the Moodle account may have changed, and the error still reaches the caller', async (_, send) => {
  vi.spyOn(api, 'post').mockRejectedValue(failure(422))
  const { heard, stop } = listen()

  await expect(send()).rejects.toBeInstanceOf(AxiosError)

  expect(heard).toHaveBeenCalledOnce()
  stop()
})

it('other failures and successes of those calls leave the account alone', async () => {
  const { heard, stop } = listen()
  vi.spyOn(api, 'post').mockRejectedValueOnce(failure(502)).mockResolvedValueOnce({ data: { moodle_course_id: 6 } })

  await expect(publishToMoodle('m', 0)).rejects.toBeInstanceOf(AxiosError)
  await linkMoodleCourse('c', 6)

  expect(heard).not.toHaveBeenCalled()
  stop()
})

it('linking the account says it changed, and "Không" is sent to the server', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const { heard, stop } = listen()

  await linkMoodleAccount()
  await dismissMoodlePrompt()

  expect(heard).toHaveBeenCalledOnce()
  expect(post).toHaveBeenLastCalledWith('/moodle/account/prompt/dismiss')
  stop()
})
