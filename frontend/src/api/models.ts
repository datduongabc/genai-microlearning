import { api } from './client'

export interface ModelChoice {
  alias: string
  label: string
  provider: string
  note: string | null
}

export interface ModelList {
  models: ModelChoice[]
  // Mặc định đã lưu của chính giảng viên đang đăng nhập; null nghĩa là chưa chọn, khi đó
  // backend dùng model trong cấu hình hệ thống.
  default: string | null
}

export async function listModels() {
  const { data } = await api.get<ModelList>('/models')
  return data
}

export async function setDefaultModel(alias: string | null) {
  const { data } = await api.put<ModelList>('/models/default', { alias })
  return data
}
