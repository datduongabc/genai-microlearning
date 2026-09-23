import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MonitoringPage } from './MonitoringPage'
import { AuthProvider } from '../auth/AuthContext'
import * as api from '../api'
import type { FailedJobInfo } from '../api'

vi.mock('../api')

const sampleJob: FailedJobInfo = {
  id: 'job-1',
  job_type: 'document_pipeline',
  step: 'extract',
  attempts: 3,
  max_attempts: 5,
  last_error: 'boom: unreadable pdf',
  created_at: '2026-01-01T00:00:00Z',
  document_filename: 'syllabus.pdf',
  course_name: 'AI 101',
  module_id: null,
}

function renderPage() {
  return render(
    <AuthProvider>
      <MemoryRouter>
        <MonitoringPage />
      </MemoryRouter>
    </AuthProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MonitoringPage', () => {
  it('shows a friendly message when there are no failed jobs', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([])

    renderPage()

    expect(await screen.findByText(/không có job nào thất bại/i)).toBeInTheDocument()
  })

  it('lists failed jobs with course/document context and the error message', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([sampleJob])

    renderPage()

    expect(await screen.findByText(/AI 101 \/ syllabus\.pdf/)).toBeInTheDocument()
    expect(screen.getByText(/boom: unreadable pdf/)).toBeInTheDocument()
    expect(screen.getByText(/3\/5 lần thử/)).toBeInTheDocument()
  })

  it('refetches with the job_type filter when the dropdown changes', async () => {
    vi.mocked(api.listFailedJobs).mockResolvedValue([sampleJob])

    renderPage()
    await screen.findByText(/AI 101 \/ syllabus\.pdf/)

    await userEvent.selectOptions(screen.getByLabelText(/loại job/i), 'module_generation')

    await waitFor(() => {
      expect(api.listFailedJobs).toHaveBeenLastCalledWith('module_generation')
    })
  })

  it('says so when the list cannot be loaded, and loads again when refreshed', async () => {
    vi.mocked(api.listFailedJobs).mockRejectedValueOnce(new Error('network')).mockResolvedValue([sampleJob])

    renderPage()
    expect(await screen.findByText(/không tải được danh sách lỗi/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /làm mới/i }))

    expect(await screen.findByText(/AI 101 \/ syllabus\.pdf/)).toBeInTheDocument()
    expect(screen.queryByText(/không tải được danh sách lỗi/i)).not.toBeInTheDocument()
    expect(api.listFailedJobs).toHaveBeenCalledTimes(2)
  })
})
