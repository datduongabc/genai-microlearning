import { api } from './client'

export interface Course {
  id: string
  name: string
  created_at: string
  moodle_course_id: number | null
}

export async function listCourses() {
  const { data } = await api.get<Course[]>('/courses')
  return data
}

export async function createCourse(name: string) {
  const { data } = await api.post<Course>('/courses', { name })
  return data
}

export async function renameCourse(courseId: string, name: string) {
  const { data } = await api.patch<Course>(`/courses/${courseId}`, { name })
  return data
}

export async function deleteCourse(courseId: string) {
  await api.delete(`/courses/${courseId}`)
}
