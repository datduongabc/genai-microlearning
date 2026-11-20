import { api } from './client'

export type MoodleStatus = 'linked' | 'not_linked'

export interface AdminUser {
  id: string
  email: string
  name: string
  is_admin: boolean
  is_active: boolean
  created_at: string
  // Null với admin: tài khoản đã có liên kết Moodle lưu sẵn hay chưa.
  moodle_status: MoodleStatus | null
  // Null với admin: số môn học tài khoản sở hữu.
  course_count: number | null
}

export interface FailedJobInfo {
  id: string
  job_type: 'document_pipeline' | 'module_generation' | 'flashcard_generation' | 'video_script' | 'video_images' | 'video_render'
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

// Full totals for the tab and filter labels, whatever filter or cap the lists use.
export interface AdminCounts {
  users: number
  inactive_users: number
  failed_jobs: Partial<Record<FailedJobInfo['job_type'], number>>
}

export async function getAdminCounts() {
  const { data } = await api.get<AdminCounts>('/admin/counts')
  return data
}

export async function listUsers(isActive: boolean) {
  const { data } = await api.get<AdminUser[]>('/admin/users', { params: { is_active: isActive } })
  return data
}

export async function setUserActive(userId: string, active: boolean) {
  const { data } = await api.post<AdminUser>(`/admin/users/${userId}/${active ? 'enable' : 'disable'}`)
  return data
}

export async function listFailedJobs(jobTypes?: readonly FailedJobInfo['job_type'][]) {
  // Repeated as job_type=a&job_type=b, which the server reads as a list (axios would send job_type[]).
  const params = new URLSearchParams((jobTypes ?? []).map((t) => ['job_type', t]))
  const { data } = await api.get<FailedJobInfo[]>('/admin/jobs/failed', { params })
  return data
}

export async function retryFailedJobs(jobIds: string[]) {
  // `reasons` names the skipped jobs that only the video editor may retry, with why.
  const { data } = await api.post<{ retried: string[]; skipped: string[]; reasons: Record<string, string> }>('/admin/jobs/retry', {
    job_ids: jobIds,
  })
  return data
}

export async function createUser(name: string, email: string, password: string) {
  const { data } = await api.post<AdminUser>('/admin/users', { name, email, password })
  return data
}
export async function resetUserPassword(userId: string, password: string) {
  await api.post(`/admin/users/${userId}/password`, { password })
}


export async function unlinkUserMoodleAccount(userId: string) {
  await api.post(`/admin/users/${userId}/moodle-unlink`)
}

// The person's data goes for good (app/api/admin.py, erase_user); the account stays, locked and nameless.
export async function eraseUser(userId: string) {
  await api.post(`/admin/users/${userId}/erase`)
}
