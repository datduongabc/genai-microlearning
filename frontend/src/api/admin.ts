import { api } from './client'

export type MoodleStatus = 'linked' | 'not_found' | 'ambiguous' | 'unknown'

export interface AdminUser {
  id: string
  email: string
  name: string
  role: 'teacher' | 'admin'
  is_active: boolean
  created_at: string
  // Chỉ có với giảng viên: có đúng một tài khoản Moodle cùng email hay không.
  moodle_status: MoodleStatus | null
}

export interface FailedJobInfo {
  id: string
  job_type: 'document_pipeline' | 'module_generation'
  step: string
  attempts: number
  max_attempts: number
  last_error: string | null
  created_at: string
  document_filename: string
  course_name: string
  owner_name: string
  owner_email: string
  module_id: string | null
}

export async function listUsers() {
  const { data } = await api.get<AdminUser[]>('/admin/users')
  return data
}

export async function createLecturer(name: string, email: string, password: string) {
  const { data } = await api.post<AdminUser>('/admin/users', { name, email, password })
  return data
}

export async function setUserActive(userId: string, active: boolean) {
  const { data } = await api.post<AdminUser>(`/admin/users/${userId}/${active ? 'enable' : 'disable'}`)
  return data
}

export async function resetUserPassword(userId: string, password: string) {
  await api.post(`/admin/users/${userId}/password`, { password })
}

export async function listFailedJobs(jobType?: FailedJobInfo['job_type']) {
  const { data } = await api.get<FailedJobInfo[]>('/admin/jobs/failed', {
    params: jobType ? { job_type: jobType } : undefined,
  })
  return data
}

export async function retryFailedJobs(jobIds: string[]) {
  const { data } = await api.post<{ retried: string[]; skipped: string[] }>('/admin/jobs/retry', {
    job_ids: jobIds,
  })
  return data
}
