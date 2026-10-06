import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { build } from 'vite'

// Exercise the actual App and effects with its production bundler and a DOM.
await build({
  configFile: false, logLevel: 'silent',
  define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('') },
  build: {
    outDir: '.test-dist/gate', emptyOutDir: true,
    lib: { entry: 'src/App.tsx', formats: ['es'], fileName: () => 'App.mjs' },
    rollupOptions: { external: ['react', 'react/jsx-runtime', 'react-dom'] },
  },
  esbuild: { jsx: 'automatic' },
})
const { default: App } = await import('../.test-dist/gate/App.mjs')
const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/?year=2026&month=10' })
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document, FormData: dom.window.FormData,
  IS_REACT_ACT_ENVIRONMENT: true,
})
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
let nextTimer = 0
const intervals = new Map()
const timeouts = new Map()
window.setInterval = (callback, delay) => { const id = ++nextTimer; intervals.set(id, { callback, delay }); return id }
window.clearInterval = (id) => intervals.delete(id)
window.setTimeout = (callback, delay) => { const id = ++nextTimer; timeouts.set(id, { callback, delay }); return id }
window.clearTimeout = (id) => timeouts.delete(id)
const user = { username: 'owner', expires_at: '2030-06-30T00:00:00+00:00' }
const response = (body, status = 200) => new Response(JSON.stringify(body), { status })
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const settings = (title = 'Prywatny gabinet') => ({ application_title: title, locations: [] })
const data = (url) => {
  if (url.includes('/application-settings')) return settings()
  if (url.includes('/work-summary')) return { year: 2026, month: 10, total_duration_seconds: 0, work_days: 0, anomaly_count: 0, days: [] }
  if (url.includes('/pay-summary')) return { year: 2026, month: 10, currency: 'PLN', total_duration_seconds: 0, work_days: 0, total_pay: '1234.56', days: [], rates_used: [] }
  if (url.includes('/pay-rates')) return []
  if (url.includes('/dashboard')) return {
    status: 'outside', generated_at: '2026-10-06T08:00:00Z', current_session: null,
    today: { date: '2026-10-06', completed_duration_seconds: 0, running_duration_seconds: null, effective_duration_seconds: 0 },
    month: { year: 2026, month: 10, completed_duration_seconds: 0, work_days: 0, pay: '1234.56', currency: 'PLN' },
  }
  throw new Error(`Unexpected domain request ${url}`)
}
let root
let requests
let handler
const container = document.getElementById('root')
const text = () => container.textContent
const isProtected = () => container.querySelector('.month-navigation') !== null
const flush = async () => { await act(async () => { await new Promise((resolve) => setImmediate(resolve)) }) }
async function mount(onRequest, strict = false) {
  requests = []
  handler = onRequest
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init })
    if (url.endsWith('/auth/logout')) return new Response(null, { status: 204 })
    if (url.endsWith('/auth/login')) return response(user)
    return await handler(url, init)
  }
  root = createRoot(container)
  await act(async () => { root.render(strict ? React.createElement(React.StrictMode, null, React.createElement(App)) : React.createElement(App)) })
  await flush()
}
async function unmount() {
  await act(async () => root.unmount())
  assert.equal(intervals.size, 0, 'all auth and domain interval effects are cleaned up')
  assert.equal(timeouts.size, 0, 'midnight refresh is cleaned up')
}
async function login() {
  const form = container.querySelector('.auth-form')
  assert.ok(form, 'login form is reachable')
  form.elements.username.value = 'owner'
  form.elements.password.value = 'public-ui-test-password'
  await act(async () => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })))
  await flush()
}
async function logout() {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent === 'Wyloguj')
  assert.ok(button, 'logout is available')
  await act(async () => button.click())
  await flush()
}
const domainRequests = () => requests.filter(({ url }) => !url.includes('/api/auth/'))

// Initial check gates all ordinary effects, then anonymous response shows only login.
const initial = deferred()
await mount((url) => url.endsWith('/auth/me') ? initial.promise : response(data(url)))
assert.match(text(), /Sprawdzanie sesji/)
assert.equal(domainRequests().length, 0)
assert.equal(isProtected(), false)
await act(async () => initial.resolve(response({}, 401)))
assert.ok(container.querySelector('.auth-form'))
assert.equal(domainRequests().length, 0)
assert.equal(isProtected(), false)
await login()
assert.equal(isProtected(), true, 'successful login mounts Work Tracker')
assert.match(text(), /Prywatny gabinet/)
assert.ok(domainRequests().length > 0)
assert.ok(requests.every(({ init }) => init.credentials === 'include'), 'all APIs carry the opaque cookie')
await logout()
assert.equal(isProtected(), false)
assert.doesNotMatch(text(), /Prywatny gabinet|1234/)
assert.ok(container.querySelector('.auth-form'))
assert.ok(![...intervals.values()].some(({ delay }) => delay === 30_000), 'dashboard polling stops')
await unmount()

// Session restoration, loss from any ordinary request, late 200/401, and relogin.
for (const failingPath of ['/dashboard', '/application-settings', '/pay-summary', '/work-summary', '/pay-rates']) {
  const failed = deferred()
  const lateSettings = deferred()
  let firstSettings = true
  await mount((url) => {
    if (url.endsWith('/auth/me')) return response(user)
    if (url.includes(failingPath)) return failed.promise
    if (url.includes('/application-settings') && firstSettings) {
      firstSettings = false
      return lateSettings.promise
    }
    return response(data(url))
  })
  assert.equal(isProtected(), true, 'valid restored session mounts Work Tracker')
  await act(async () => failed.resolve(response({}, 401)))
  assert.equal(isProtected(), false, `global auth loss from ${failingPath}`)
  assert.match(text(), /Sesja wygasła\. Zaloguj się ponownie\./)
  assert.ok(![...intervals.values()].some(({ delay }) => delay === 30_000))
  const count = domainRequests().length
  await act(async () => {
    for (const { callback } of intervals.values()) callback()
    window.dispatchEvent(new window.Event('focus'))
  })
  assert.equal(domainRequests().length, count, 'anonymous gate does not poll domain data')
  await act(async () => lateSettings.resolve(response(settings('STARE DANE'))))
  assert.equal(isProtected(), false)
  assert.doesNotMatch(text(), /STARE DANE/)
  handler = (url) => url.endsWith('/auth/me') ? response(user) : response(data(url))
  await login()
  assert.equal(isProtected(), true, 'login remains usable after auth loss')
  assert.match(text(), /Prywatny gabinet/)
  await unmount()
}

// Responses from a logged-out lifetime must not affect a subsequent login.
for (const status of [200, 401]) {
  const late = deferred()
  await mount((url) => url.endsWith('/auth/me') ? response(user)
    : url.includes('/application-settings') ? late.promise : response(data(url)))
  assert.equal(isProtected(), true)
  await logout()
  assert.equal(isProtected(), false)
  assert.doesNotMatch(text(), /Prywatny gabinet|1234/)
  handler = (url) => url.endsWith('/auth/me') ? response(user) : response(data(url))
  await login()
  await act(async () => late.resolve(response(settings('STARE DANE'), status)))
  assert.equal(isProtected(), true)
  assert.match(text(), /Prywatny gabinet/)
  assert.doesNotMatch(text(), /STARE DANE|Sesja wygasła/)
  await unmount()
}

// An export request is also part of the global 401 boundary.
await mount((url) => url.endsWith('/auth/me') ? response(user)
  : url.includes('/export/') ? response({}, 401) : response(data(url)))
await act(async () => [...container.querySelectorAll('button')].find((button) => button.textContent === 'Pobierz CSV').click())
assert.equal(isProtected(), false)
assert.match(text(), /Sesja wygasła/)
await unmount()

// A delayed /me must not erase a newer successful login.
const staleMe = deferred()
let checks = 0
await mount((url) => url.endsWith('/auth/me')
  ? checks++ === 0 ? response(user) : staleMe.promise : response(data(url)))
await act(async () => window.dispatchEvent(new window.Event('focus')))
await logout()
await login()
await act(async () => staleMe.resolve(response({}, 401)))
assert.equal(isProtected(), true)
await unmount()
// Production uses StrictMode in development: discarded effect lifetimes stay closed.
const strictCheck = deferred()
// Each HTTP call has its own body, even when both are released together.
await mount((url) => url.endsWith('/auth/me') ? strictCheck.promise.then(() => response(user)) : response(data(url)), true)
assert.equal(domainRequests().length, 0)
await act(async () => strictCheck.resolve(response(user)))
assert.equal(isProtected(), true)
await logout()
assert.equal(isProtected(), false)
await unmount()
dom.window.close()
console.log('Rendered authentication gate tests passed.')
