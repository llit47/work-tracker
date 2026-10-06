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
assert(state.user === null && state.error !== '', 'failed logout hides protected state and reports failure')
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
assert(state.user === null && state.error === 'Sesja wygasła. Zaloguj się ponownie.', 'expired session returns to login with explanation')
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

// Every domain fetch belongs to a single authenticated lifetime.
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
async function expectStale(request: Promise<unknown>, message: string) {
  let rejected = false
  try { await request } catch (error) { rejected = error instanceof DOMException && error.name === 'AbortError' }
  assert(rejected, message)
}
let gated: AuthState = INITIAL_AUTH_STATE
const gatedAuth = createAuthController(async () => ({ ok: true, status: 200, json: async () => user }), '', (next) => { gated = next })
let domainCalls = 0
const pendingHeaders = deferred<Response>()
const beforeCheck = gatedAuth.protect(async () => { domainCalls += 1; return pendingHeaders.promise })
await expectStale(beforeCheck('/api/dashboard'), 'domain calls fail closed during initial auth check')
assert(domainCalls === 0, 'ordinary requests do not start before authentication')
await gatedAuth.refresh()
const scoped = gatedAuth.protect(async () => { domainCalls += 1; return pendingHeaders.promise })
const oldData = scoped('/api/dashboard')
await gatedAuth.logout()
pendingHeaders.resolve(new Response('{}'))
await expectStale(oldData, 'old domain response is rejected after logout')
assert(gated.user === null, 'late response does not restore user')
await gatedAuth.login('owner', 'public-test-password')
const oldUnauthorized = deferred<Response>()
const oldFetch = gatedAuth.protect(async () => oldUnauthorized.promise)
const old401 = oldFetch('/api/pay-summary')
await gatedAuth.logout()
await gatedAuth.login('owner', 'public-test-password')
oldUnauthorized.resolve(new Response('{}', { status: 401 }))
await expectStale(old401, 'stale 401 is rejected without affecting new session')
assert(gated.user?.username === 'owner', 'late 401 cannot log out a newer login')

const pendingBody = deferred<unknown>()
const decodeFetch = gatedAuth.protect(async () => ({ ok: true, status: 200, json: async () => pendingBody.promise }))
const decoding = (await decodeFetch('/api/application-settings')).json()
gatedAuth.loseSession()
pendingBody.resolve({ application_title: 'Stale protected title' })
await expectStale(decoding, 'body decoding cannot restore protected state after auth loss')
assert(gated.error === 'Sesja wygasła. Zaloguj się ponownie.', 'global auth loss has clear Polish message')
await gatedAuth.login('owner', 'public-test-password')
const unauthorized = gatedAuth.protect(async () => new Response('{}', { status: 401 }))
await expectStale(unauthorized('/api/export/monthly.pdf'), 'export 401 is global auth loss too')
assert(gated.user === null, 'domain 401 clears authenticated state')
const protectedAfterLoss = gatedAuth.protect(async () => { domainCalls += 1; return new Response('{}') })
const count = domainCalls
await expectStale(protectedAfterLoss('/api/dashboard'), 'new domain requests stop after auth loss')
assert(domainCalls === count, 'no background request after auth loss')
console.log('Global 401 and stale response tests passed.')
