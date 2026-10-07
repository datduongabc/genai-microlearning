import { api } from './client'

export interface UserInfo {
  id: string
  email: string
  name: string
  role: string
}

// A lecturer's Moodle username and password, or an admin's email and local password.
export async function login(username: string, password: string) {
  const { data } = await api.post<{
    access_token: string
    refresh_token: string
    token_type: string
    user: UserInfo
  }>('/auth/login', { username, password })
  return data
}

export async function logout(refreshToken: string) {
  // Best-effort -- nếu API lỗi thì vẫn xóa auth local ở AuthContext.logout(),
  // không chặn người dùng đăng xuất được vì 1 request mạng thất bại.
  await api.post('/auth/logout', { refresh_token: refreshToken }).catch(() => {})
}
