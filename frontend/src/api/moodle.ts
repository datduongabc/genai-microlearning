import axios from 'axios'
import { api } from './client'

export interface MoodleCourse {
  id: number
  fullname: string
  shortname: string
}

export interface MoodleSection {
  sectionnum: number
  name: string
}

// Every course that exists in Moodle (minus those already linked here or claimed by another lecturer).
export async function listMoodleCourses() {
  const { data } = await api.get<MoodleCourse[]>('/courses/moodle/courses')
  return data
}

export type MoodleAccountStatus = 'not_linked' | 'linked'

export async function getMoodleAccount() {
  const { data } = await api.get<{ email: string; status: MoodleAccountStatus }>('/moodle/account')
  return data
}

// Raised on window when the lecturer's Moodle account may have changed: they linked it, or the
// server refused a Moodle action (422) because the link must be made again. Whatever shows the
// account (the "Tài khoản Moodle" section) asks the server again.
export const MOODLE_ACCOUNT_CHANGED_EVENT = 'moodle-account-changed'

function accountChanged() {
  window.dispatchEvent(new Event(MOODLE_ACCOUNT_CHANGED_EVENT))
}

async function noticingRelink<T>(request: Promise<T>): Promise<T> {
  try {
    return await request
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 422) accountChanged()
    throw err
  }
}

// Links the Moodle account with this username and password: the server asks Moodle whether they
// are right (422 when not) and uses the password for that one check only.
export async function linkMoodleAccount(username: string, password: string) {
  const { data } = await api.post<{ email: string; status: 'linked' }>('/moodle/account/link', { username, password })
  accountChanged()
  return data
}

export async function linkMoodleCourse(courseId: string, moodleCourseId: number) {
  const { data } = await noticingRelink(
    api.post<{ moodle_course_id: number }>(`/courses/${courseId}/link-moodle`, { moodle_course_id: moodleCourseId }),
  )
  return data
}

export async function createMoodleCourseFor(courseId: string) {
  const { data } = await noticingRelink(api.post<{ moodle_course_id: number }>(`/courses/${courseId}/create-moodle`))
  return data
}

export async function unlinkMoodleCourse(courseId: string) {
  await api.delete(`/courses/${courseId}/link-moodle`)
}

export async function getMoodleSections(courseId: string) {
  const { data } = await api.get<MoodleSection[]>(`/courses/${courseId}/moodle-sections`)
  return data
}

export interface PublishToMoodleResult {
  moodle_course_id: number
  book_cmid: number
  practice_cmid?: number
  quiz_cmid?: number
}

export async function publishToMoodle(moduleId: string, sectionNum: number) {
  const { data } = await noticingRelink(
    api.post<PublishToMoodleResult>(`/modules/${moduleId}/publish-to-moodle`, { section_num: sectionNum }),
  )
  return data
}
