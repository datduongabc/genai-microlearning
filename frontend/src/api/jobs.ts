import { api } from './client'

// "Thử lại" của giảng viên trên công việc lỗi của chính mình (tài liệu hoặc sinh mô-đun): job cũ
// chạy lại từ bước đã dừng, không tải lên lại và không tạo thêm mô-đun.
export async function retryJob(jobId: string) {
  const { data } = await api.post<{ id: string; status: string }>(`/jobs/${jobId}/retry`)
  return data
}
