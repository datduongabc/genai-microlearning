import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from './client'
import {
  createMoodleCourseFor,
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

it('linking the account sends the username and password once and says the account changed', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { email: 'lan@x.edu', status: 'linked' } })
  const { heard, stop } = listen()

  await linkMoodleAccount('lan.nguyen', 'Moodle-pass-1')

  expect(post).toHaveBeenCalledExactlyOnceWith('/moodle/account/link', { username: 'lan.nguyen', password: 'Moodle-pass-1' })
  expect(heard).toHaveBeenCalledOnce()
  stop()
})

it('a link the server refuses (wrong credentials) reaches the caller and changes nothing', async () => {
  vi.spyOn(api, 'post').mockRejectedValue(failure(422))
  const { heard, stop } = listen()

  await expect(linkMoodleAccount('lan.nguyen', 'wrong')).rejects.toBeInstanceOf(AxiosError)

  expect(heard).not.toHaveBeenCalled()
  stop()
})
