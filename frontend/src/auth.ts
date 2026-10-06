import type { FetchLike } from './pay.js'

export type AuthUser = { username: string; expires_at: string }
export type AuthState = { user: AuthUser | null; loading: boolean; busy: boolean; error: string }
export const INITIAL_AUTH_STATE: AuthState = { user: null, loading: true, busy: false, error: '' }
export const AUTH_REFRESH_INTERVAL_MS = 5 * 60_000

export function createAuthController(
  fetcher: FetchLike,
  apiBase: string,
  onChange: (state: AuthState) => void,
) {
  let state = { ...INITIAL_AUTH_STATE }
  let version = 0
  let disposed = false
  let refreshing = false
  const publish = (patch: Partial<AuthState>) => {
    state = { ...state, ...patch }
    if (!disposed) onChange(state)
  }
  return {
    async refresh() {
      if (disposed || refreshing || state.busy) return
      refreshing = true
      const requestedVersion = version
      try {
        const response = await fetcher(`${apiBase}/api/auth/me`, { credentials: 'include' })
        if (!response.ok && response.status !== 401) throw new Error('Session unavailable')
        const user = response.status === 401 ? null : await response.json() as AuthUser
        if (!disposed && version === requestedVersion) publish({ user, error: '' })
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
      publish({ busy: true, loading: false, error: '' })
      try {
        const response = await fetcher(`${apiBase}/api/auth/login`, {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        })
        if (response.status === 401) {
          publish({ error: 'Nieprawidłowa nazwa użytkownika lub hasło.' })
        } else if (!response.ok) {
          throw new Error('Login unavailable')
        } else {
          publish({ user: await response.json() as AuthUser })
        }
      } catch {
        publish({ error: 'Nie udało się zalogować. Spróbuj ponownie.' })
      } finally {
        publish({ busy: false })
      }
    },
    async logout() {
      if (disposed || state.busy) return
      version += 1
      publish({ busy: true, loading: false, error: '' })
      try {
        const response = await fetcher(`${apiBase}/api/auth/logout`, { method: 'POST', credentials: 'include' })
        if (!response.ok) throw new Error('Logout unavailable')
        publish({ user: null })
      } catch {
        publish({ error: 'Nie udało się wylogować. Spróbuj ponownie.' })
      } finally {
        publish({ busy: false })
      }
    },
    dispose() { disposed = true; version += 1 },
  }
}
