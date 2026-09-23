import { api } from './client'

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
  module_id: string | null
}

export async function listFailedJobs(jobType?: FailedJobInfo['job_type']) {
  const { data } = await api.get<FailedJobInfo[]>('/jobs/failed', {
    params: jobType ? { job_type: jobType } : undefined,
  })
  return data
}
