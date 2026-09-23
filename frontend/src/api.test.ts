import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, apiErrorMessage, navigation } from './api'

function failure(status: number, data: unknown = {}, requestConfig?: InternalAxiosRequestConfig) {
  const config = requestConfig ?? ({ headers: {} } as InternalAxiosRequestConfig)
  const response = { status, statusText: '', data, headers: {}, config } as AxiosResponse
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, null, response)
}

describe('apiErrorMessage', () => {
  it('uses the message the server sent', () => {
    expect(apiErrorMessage(failure(409, { detail: 'Tài liệu đang xử lý' }), 'fallback')).toBe('Tài liệu đang xử lý')
  })

  it('joins the messages of a validation error and drops the technical prefix', () => {
    const error = failure(422, {
      detail: [
        { msg: 'Value error, Họ tên không được để trống', loc: ['body', 'name'] },
        { msg: 'String should have at most 100 characters', loc: ['body', 'name'] },
      ],
    })

    expect(apiErrorMessage(error, 'fallback')).toBe(
      'Họ tên không được để trống. String should have at most 100 characters',
    )
  })

  it('explains a refused request rate in words', () => {
    expect(apiErrorMessage(failure(429, { error: 'Rate limit exceeded: 10 per 1 minute' }), 'fallback')).toMatch(
      /quá nhanh/i,
    )
  })

  it('shows the wait the server states when it locks an account out', () => {
    const detail = 'Đăng nhập sai quá nhiều lần, vui lòng thử lại sau 1 phút.'

    expect(apiErrorMessage(failure(429, { detail }), 'fallback')).toBe(detail)
  })

  it('explains a body that is too large', () => {
    expect(apiErrorMessage(failure(413, { detail: 'Request body too large' }), 'fallback')).toMatch(/quá lớn/i)
  })

  it.each([
    ['a network error', new Error('Network Error')],
    ['a body with no message', failure(500, { something: 'else' })],
    ['an empty message', failure(400, { detail: '' })],
    ['an empty list of messages', failure(422, { detail: [] })],
    ['nothing at all', undefined],
  ])('falls back for %s', (_name, error) => {
    expect(apiErrorMessage(error, 'fallback')).toBe('fallback')
  })
})

describe('the session refresh behind every request', () => {
  const originalAdapter = api.defaults.adapter
  let refresh: ReturnType<typeof vi.spyOn>
  let toLogin: ReturnType<typeof vi.spyOn>
  let seen: string[]

  function store(token: string, refreshToken: string) {
    localStorage.setItem('auth', JSON.stringify({ token, refreshToken }))
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    localStorage.clear()
    seen = []
    toLogin = vi.spyOn(navigation, 'toLogin').mockImplementation(() => {})
    refresh = vi.spyOn(axios, 'post')
    // The server accepts only the access token 'good'; anything else is a 401.
    api.defaults.adapter = async (config) => {
      const bearer = String(config.headers.Authorization ?? '')
      seen.push(bearer)
      if (bearer === 'Bearer good') {
        return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse
      }
      throw failure(401)
    }
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    api.defaults.adapter = originalAdapter
  })

  it('renews an expired access token once and repeats the request with the new one', async () => {
    store('stale', 'refresh-1')
    refresh.mockResolvedValue({ data: { access_token: 'good', refresh_token: 'refresh-2' } })

    const response = await api.get('/courses')

    expect(response.status).toBe(200)
    expect(seen).toEqual(['Bearer stale', 'Bearer good'])
    expect(JSON.parse(localStorage.getItem('auth')!)).toEqual({ token: 'good', refreshToken: 'refresh-2' })
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('renews only once when several requests find the token expired together', async () => {
    store('stale', 'refresh-1')
    refresh.mockResolvedValue({ data: { access_token: 'good', refresh_token: 'refresh-2' } })

    const responses = await Promise.all([api.get('/a'), api.get('/b'), api.get('/c')])

    expect(responses.map((r) => r.status)).toEqual([200, 200, 200])
    expect(refresh).toHaveBeenCalledTimes(1)
  })

  it('signs the user out when the server says the session is over', async () => {
    store('stale', 'refresh-1')
    refresh.mockRejectedValue(failure(401))

    const request = api.get('/courses')
    const settled = request.catch((error) => error)
    await vi.advanceTimersByTimeAsync(1000)

    expect((await settled).response.status).toBe(401)
    expect(localStorage.getItem('auth')).toBeNull()
    expect(toLogin).toHaveBeenCalled()
  })

  it('keeps the session when the server cannot be reached at all', async () => {
    store('stale', 'refresh-1')
    refresh.mockRejectedValue(new AxiosError('Network Error', 'ERR_NETWORK'))

    const request = api.get('/courses')
    const settled = request.catch((error) => error)
    await vi.advanceTimersByTimeAsync(1000)

    await settled
    expect(localStorage.getItem('auth')).not.toBeNull()
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('keeps the session when the server is failing rather than refusing', async () => {
    store('stale', 'refresh-1')
    refresh.mockRejectedValue(failure(503))

    const settled = api.get('/courses').catch((error) => error)
    await vi.advanceTimersByTimeAsync(1000)

    await settled
    expect(localStorage.getItem('auth')).not.toBeNull()
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('carries on with the tokens another tab stored when that tab spent the refresh token first', async () => {
    store('stale', 'refresh-1')
    refresh.mockImplementation(async () => {
      // The other tab won the race and stored its result while this refresh was answered with a 401.
      store('good', 'refresh-from-other-tab')
      throw failure(401)
    })

    const request = api.get('/courses')
    await vi.advanceTimersByTimeAsync(1000)

    expect((await request).status).toBe(200)
    expect(seen).toEqual(['Bearer stale', 'Bearer good'])
    expect(localStorage.getItem('auth')).toContain('refresh-from-other-tab')
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('does not try to renew when there is no session to renew', async () => {
    const settled = api.get('/courses').catch((error) => error)
    await vi.advanceTimersByTimeAsync(1000)

    expect((await settled).response.status).toBe(401)
    expect(refresh).not.toHaveBeenCalled()
  })

  it('does not treat a wrong password as an expired session', async () => {
    store('stale', 'refresh-1')
    api.defaults.adapter = async (config) => {
      throw failure(401, { detail: 'Invalid email or password' }, config)
    }

    const settled = api.post('/auth/login', {}).catch((error) => error)
    await vi.advanceTimersByTimeAsync(1000)

    await settled
    expect(refresh).not.toHaveBeenCalled()
  })
})
