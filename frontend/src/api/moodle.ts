import { api } from './client'

export interface MoodleCourseSearchResult {
  id: number
  fullname: string
  shortname: string
}

export interface MoodleSection {
  sectionnum: number
  name: string
}

export async function searchMoodleCourses(q: string) {
  const { data } = await api.get<MoodleCourseSearchResult[]>('/courses/moodle/search', { params: { q } })
  return data
}

export async function linkMoodleCourse(courseId: string, moodleCourseId: number) {
  const { data } = await api.post<{ moodle_course_id: number }>(`/courses/${courseId}/link-moodle`, {
    moodle_course_id: moodleCourseId,
  })
  return data
}

export async function createMoodleCourseFor(courseId: string) {
  const { data } = await api.post<{ moodle_course_id: number }>(`/courses/${courseId}/create-moodle`)
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
  const { data } = await api.post<PublishToMoodleResult>(`/modules/${moduleId}/publish-to-moodle`, {
    section_num: sectionNum,
  })
  return data
}
