import axios, { type InternalAxiosRequestConfig } from 'axios'

export const api = axios.create({ baseURL: '/api' })

interface StoredAuth {
  token: string | null
  refreshToken: string | null
}

function readStoredAuth(): StoredAuth {
  const raw = localStorage.getItem('auth')
  if (!raw) return { token: null, refreshToken: null }
  const parsed = JSON.parse(raw) as { token: string | null; refreshToken?: string | null }
  return { token: parsed.token ?? null, refreshToken: parsed.refreshToken ?? null }
}

function writeStoredAuth(patch: Partial<StoredAuth>) {
  const raw = localStorage.getItem('auth')
  const current = raw ? JSON.parse(raw) : {}
  localStorage.setItem('auth', JSON.stringify({ ...current, ...patch }))
}

api.interceptors.request.use((config) => {
  const { token } = readStoredAuth()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// access token sống 1h (trước là 7 ngày) -- 1 request 401 vì hết hạn giữa
// phiên làm việc giờ là chuyện BÌNH THƯỜNG, không phải lỗi auth thật. Tự
// refresh 1 lần rồi phát lại đúng request gốc thay vì đá thẳng người dùng
// về /login. Gộp các lần refresh chồng nhau (nhiều request 401 cùng lúc)
// thành 1 lệnh gọi /auth/refresh duy nhất qua refreshPromise dùng chung.
let refreshPromise: Promise<string | null | undefined> | null = null

// Wrapped in an object so a test can watch it: assigning window.location is not
// something a test can observe.
export const navigation = {
  toLogin() {
    window.location.href = '/login'
  },
}

// A refresh token works once. A second tab presenting the same one a moment after the
// first is refused by the server without ending the session, and the tab that won has
// stored its new tokens by then or within moments, so wait that long and look there
// before deciding the session is over.
const OTHER_TAB_GRACE_MS = 300
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// A token: renewed. null: the session is over. undefined: could not tell (the server
// did not answer or is failing), which must not sign anybody out.
async function refreshAccessToken(): Promise<string | null | undefined> {
  const { refreshToken } = readStoredAuth()
  if (!refreshToken) return null
  try {
    const { data } = await axios.post<{ access_token: string; refresh_token: string }>(
      '/api/auth/refresh',
      { refresh_token: refreshToken },
    )
    writeStoredAuth({ token: data.access_token, refreshToken: data.refresh_token })
    return data.access_token
  } catch (error) {
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (status === undefined || status >= 500) return undefined
    await sleep(OTHER_TAB_GRACE_MS)
    const latest = readStoredAuth()
    return latest.refreshToken && latest.refreshToken !== refreshToken ? latest.token : null
  }
}

function sharedRefresh() {
  refreshPromise ??= refreshAccessToken().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

function endSession() {
  if (window.location.pathname !== '/login') {
    localStorage.removeItem('auth')
    navigation.toLogin()
  }
}

// For what axios cannot do in a browser: reading a response as it arrives. Same session
// rules as the interceptors below.
export async function fetchWithAuth(path: string, init: RequestInit): Promise<Response> {
  const send = (token: string | null) => fetch(`/api${path}`, {
    ...init,
    headers: { ...init.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  })
  const response = await send(readStoredAuth().token)
  if (response.status !== 401) return response
  const newAccessToken = await sharedRefresh()
  if (newAccessToken) {
    const retried = await send(newAccessToken)
    if (retried.status !== 401) return retried
    endSession()
    return retried
  }
  if (newAccessToken === null) endSession()
  return response
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined
    const isAuthEndpoint = config?.url?.startsWith('/auth/')

    if (error.response?.status === 401 && config && !config._retried && !isAuthEndpoint) {
      config._retried = true
      const newAccessToken = await sharedRefresh()
      if (newAccessToken) {
        config.headers.Authorization = `Bearer ${newAccessToken}`
        return api(config)
      }
      // Could not reach the server: leave the session alone and let the caller report it.
      if (newAccessToken === undefined) return Promise.reject(error)
    }

    if (error.response?.status === 401) endSession()
    return Promise.reject(error)
  },
)

// The words to show a person for a failed request. The server answers with a message
// (`detail`) for the failures it explains, a list of them for a rejected body, and
// nothing useful for a network error or a rate limit, hence the fallback.
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (!axios.isAxiosError(error)) return fallback
  const status = error.response?.status
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail
  // The message the server wrote for a refusal it explains (a duplicate email, a weak
  // password). The rate limiter's own 429 carries none, so it gets the general wording.
  if (typeof detail === 'string' && detail && status !== 413) return detail
  if (status === 429) return 'Bạn thao tác quá nhanh, vui lòng đợi một chút rồi thử lại.'
  if (status === 413) return 'Dữ liệu gửi lên quá lớn.'
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (typeof item?.msg === 'string' ? item.msg.replace(/^Value error, /, '') : ''))
      .filter(Boolean)
    if (messages.length > 0) return messages.join('. ')
  }
  return fallback
}
