import { api } from './client'

export interface UserInfo {
  id: string
  email: string
  name: string
  is_admin: boolean
  // False for a student account an LMS launch made: it may only use the course chat.
  can_author?: boolean
}

// Local credentials for every account.
export async function login(email: string, password: string) {
  const { data } = await api.post<{
    access_token: string
    refresh_token: string
    token_type: string
    user: UserInfo
  }>('/auth/login', { email, password })
  return data
}

// The one-time code an LTI launch hands to the browser (backend/app/lti/launch.py).
export async function ltiSession(code: string) {
  const { data } = await api.post<{
    access_token: string
    refresh_token: string
    token_type: string
    user: UserInfo
    course_id: string
    course_role: "owner" | "member"
  }>('/lti/session', { code })
  return data
}

export async function logout(refreshToken: string) {
  // Best-effort -- nếu API lỗi thì vẫn xóa auth local ở AuthContext.logout(),
  // không chặn người dùng đăng xuất được vì 1 request mạng thất bại.
  await api.post('/auth/logout', { refresh_token: refreshToken }).catch(() => {})
}

export async function register(name: string, email: string, password: string) {
  const { data } = await api.post<UserInfo>('/auth/register', { name, email, password })
  return data
}

interface AuthOptions {
  self_registration: boolean
}

export async function getAuthOptions() {
  const { data } = await api.get<AuthOptions>('/auth/options')
  return data
}
