import { INITIAL_AUTH_STATE, createAuthController, type AuthState } from '../src/auth.js'
import type { FetchLike } from '../src/pay.js'

function assert(value: unknown, message: string): void {
  if (!value) throw new Error(message)
}
const user = { username: 'owner', expires_at: '2030-06-30T00:00:00+00:00' }
let state: AuthState = INITIAL_AUTH_STATE
let status = 401
let failure = false
const requests: { url: string; init?: RequestInit }[] = []
const fetcher: FetchLike = async (url, init) => {
  requests.push({ url, init })
  if (failure) throw new Error('Network failure')
  return { ok: status >= 200 && status < 300, status, json: async () => user }
}
const auth = createAuthController(fetcher, '', (next) => { state = next })
await auth.refresh()
assert(!state.loading && state.user === null && state.error === '', 'anonymous state restores cleanly')
status = 200
await auth.login('owner', 'public-frontend-test-only-password')
assert(state.user?.username === 'owner' && !state.busy, 'login publishes current user')
assert(requests[1].url === '/api/auth/login', 'login uses auth endpoint')
assert(requests[1].init?.method === 'POST', 'login uses POST')
assert(JSON.parse(String(requests[1].init?.body)).username === 'owner', 'username is sent only in JSON body')
await auth.refresh()
assert(state.user?.username === 'owner', 'cookie restores user on reload/refresh')
failure = true
await auth.refresh()
assert(state.user?.username === 'owner' && state.error !== '', 'network failure keeps previous session view')
await auth.logout()
assert(state.user?.username === 'owner' && state.error !== '', 'failed logout remains retryable')
failure = false
status = 204
await auth.logout()
assert(state.user === null && state.error === '', 'logout resets user')
status = 401
await auth.login('owner', 'wrong-public-test-password')
assert(state.user === null && state.error.includes('Nieprawidłowa'), 'wrong credentials show generic Polish failure')
status = 200
await auth.login('owner', 'public-frontend-test-only-password')
status = 401
await auth.refresh()
assert(state.user === null && state.error === '', 'expired session returns to anonymous state')
assert(requests.every((request) => request.init?.credentials === 'include'), 'all auth calls send opaque browser cookies')
assert(requests.every((request) => !request.url.includes('password')), 'credentials never enter URL')

// An older /me response must not replace a successful login state.
let resolveOld: ((value: Awaited<ReturnType<FetchLike>>) => void) | undefined
const oldResponse = new Promise<Awaited<ReturnType<FetchLike>>>((resolve) => { resolveOld = resolve })
let raced: AuthState = INITIAL_AUTH_STATE
const race = createAuthController(async (url) => url.endsWith('/me') ? oldResponse : {
  ok: true, status: 200, json: async () => user,
}, '', (next) => { raced = next })
const refreshing = race.refresh()
await race.login('owner', 'public-frontend-test-only-password')
resolveOld?.({ ok: false, status: 401, json: async () => ({}) })
await refreshing
assert(raced.user?.username === 'owner', 'stale anonymous response cannot erase login state')

let calls = 0
const disposed = createAuthController(fetcher, '', () => { calls += 1 })
disposed.dispose()
await disposed.refresh()
await disposed.login('owner', 'public-frontend-test-only-password')
await disposed.logout()
assert(calls === 0, 'disposed controller cannot update UI')
console.log('Auth state/API tests passed.')
