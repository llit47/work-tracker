import type { FetchLike } from './pay.js'

export type AuthUser = { username: string; expires_at: string }
export type AuthState = { user: AuthUser | null; loading: boolean; busy: boolean; error: string }
export const INITIAL_AUTH_STATE: AuthState = { user: null, loading: true, busy: false, error: '' }
export const SESSION_EXPIRED_MESSAGE = 'Sesja wygasła. Zaloguj się ponownie.'
export const AUTH_REFRESH_INTERVAL_MS = 5 * 60_000

function browserRequest(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase())) {
    headers.set('X-Work-Tracker-Request', '1')
  }
  return { ...init, headers, credentials: 'include' }
}

export function createAuthController(
  fetcher: FetchLike,
  apiBase: string,
  onChange: (state: AuthState) => void,
) {
  let state = { ...INITIAL_AUTH_STATE }
  let version = 0
  let disposed = false
  let refreshing = false
  let heartbeatVersion: number | null = null
  let lastHeartbeatAt = -Infinity
  const publish = (patch: Partial<AuthState>) => {
    state = { ...state, ...patch }
    if (!disposed) onChange(state)
  }
  const isCurrent = (requestedVersion: number) => !disposed && version === requestedVersion
  const loseSession = () => {
    if (disposed) return
    version += 1
    lastHeartbeatAt = -Infinity
    publish({ user: null, loading: false, busy: false, error: SESSION_EXPIRED_MESSAGE })
  }
  return {
    loseSession,
    // A fetcher belongs to one authenticated lifetime. Older responses (including
    // body decoding) cannot update a new login or trigger a download after logout.
    protect<R extends { ok: boolean; status: number }>(domainFetch: (url: string, init?: RequestInit) => Promise<R>) {
      const requestedVersion = version
      return async (url: string, init?: RequestInit): Promise<R> => {
        const check = () => {
          if (!isCurrent(requestedVersion) || !state.user) throw new DOMException('Stale session', 'AbortError')
        }
        check()
        const response = await domainFetch(url, browserRequest(init))
        check()
        if (response.status === 401) {
          loseSession()
          throw new DOMException('Session expired', 'AbortError')
        }
        return new Proxy(response, {
          get(target, property) {
            const value: unknown = Reflect.get(target, property, target)
            if (typeof value !== 'function') return value
            if (property === 'json' || property === 'blob' || property === 'text') {
              return async () => {
                check()
                const body: unknown = await value.call(target)
                check()
                return body
              }
            }
            return value.bind(target)
          },
        })
      }
    },
    async activity(visible: boolean, nowMs = Date.now()) {
      if (!visible || disposed || !state.user || state.busy || heartbeatVersion === version
          || nowMs - lastHeartbeatAt < AUTH_REFRESH_INTERVAL_MS) return
      const requestedVersion = version
      heartbeatVersion = requestedVersion
      lastHeartbeatAt = nowMs
      try {
        const response = await fetcher(`${apiBase}/api/auth/heartbeat`, browserRequest({ method: 'POST' }))
        if (!isCurrent(requestedVersion)) return
        if (response.status === 401) loseSession()
        // No replay on failure. A future interaction may try again after 5 minutes.
      } catch {
        // A transient failure does not log out a still valid session.
      } finally {
        if (heartbeatVersion === requestedVersion) heartbeatVersion = null
      }
    },
    async refresh(onlyAuthenticated = false) {
      if (disposed || refreshing || state.busy || (onlyAuthenticated && !state.user)) return
      refreshing = true
      const requestedVersion = version
      try {
        const response = await fetcher(`${apiBase}/api/auth/me`, { credentials: 'include' })
        if (!response.ok && response.status !== 401) throw new Error('Session unavailable')
        const user = response.status === 401 ? null : await response.json() as AuthUser
        if (isCurrent(requestedVersion)) {
          if (!user && state.user) loseSession()
          else publish({ user, error: state.error === SESSION_EXPIRED_MESSAGE && !user ? state.error : '' })
        }
      } catch {
        if (!disposed && version === requestedVersion) publish({ error: 'Nie udało się sprawdzić sesji.' })
      } finally {
        refreshing = false
        if (!disposed && version === requestedVersion) publish({ loading: false })
      }
    },
    async login(username: string, password: string) {
      if (disposed || state.busy) return
      version += 1
      lastHeartbeatAt = -Infinity
      const requestedVersion = version
      publish({ busy: true, loading: false, error: '' })
      try {
        const response = await fetcher(`${apiBase}/api/auth/login`, browserRequest({
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        }))
        if (response.status === 401) {
          if (isCurrent(requestedVersion)) publish({ error: 'Nieprawidłowa nazwa użytkownika lub hasło.' })
        } else if (response.status === 429) {
          const retryAfter = response.headers?.get('Retry-After')
          const seconds = retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) : NaN
          const retryMessage = Number.isSafeInteger(seconds) && seconds > 0
            ? ` Możesz ponowić próbę za ${seconds} s.` : ' Spróbuj ponownie później.'
          if (isCurrent(requestedVersion)) publish({ error: `Zbyt wiele prób logowania.${retryMessage}` })
        } else if (response.status === 503) {
          if (isCurrent(requestedVersion)) publish({ error: 'Logowanie jest chwilowo niedostępne. Spróbuj ponownie później.' })
        } else if (!response.ok) {
          throw new Error('Login unavailable')
        } else {
          const user = await response.json() as AuthUser
          if (isCurrent(requestedVersion)) publish({ user })
        }
      } catch {
        if (isCurrent(requestedVersion)) publish({ error: 'Nie udało się zalogować. Spróbuj ponownie.' })
      } finally {
        if (isCurrent(requestedVersion)) publish({ busy: false })
      }
    },
    async logout() {
      if (disposed || state.busy) return
      version += 1
      const requestedVersion = version
      publish({ user: null, busy: true, loading: false, error: '' })
      try {
        const response = await fetcher(`${apiBase}/api/auth/logout`, browserRequest({ method: 'POST' }))
        if (!response.ok) throw new Error('Logout unavailable')
        if (isCurrent(requestedVersion)) publish({ user: null })
      } catch {
        if (isCurrent(requestedVersion)) publish({ error: 'Nie udało się wylogować. Spróbuj ponownie.' })
      } finally {
        if (isCurrent(requestedVersion)) publish({ busy: false })
      }
    },
    dispose() { disposed = true; version += 1 },
  }
}
