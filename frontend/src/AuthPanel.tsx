import { useEffect, useRef, useState, type FormEvent } from 'react'
import { AUTH_REFRESH_INTERVAL_MS, INITIAL_AUTH_STATE, createAuthController } from './auth'

export default function AuthPanel({ apiBase }: { apiBase: string }) {
  const [state, setState] = useState(INITIAL_AUTH_STATE)
  const controller = useRef<ReturnType<typeof createAuthController> | null>(null)
  useEffect(() => {
    const auth = createAuthController(fetch, apiBase, setState)
    controller.current = auth
    const refresh = () => {
      if (document.visibilityState === 'visible') void auth.refresh()
    }
    void auth.refresh()
    const timer = window.setInterval(refresh, AUTH_REFRESH_INTERVAL_MS)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      auth.dispose()
      controller.current = null
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [apiBase])

  const login = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    const username = String(fields.get('username') ?? '')
    const password = String(fields.get('password') ?? '')
    form.reset()
    void controller.current?.login(username, password)
  }

  return (
    <section className="settings-section" aria-labelledby="account-heading">
      <h3 id="account-heading">Konto</h3>
      <p className="settings-note">Logowanie jest opcjonalne. Dostęp do danych nie wymaga jeszcze konta.</p>
      {state.loading ? <p>Sprawdzanie sesji…</p> : state.user ? (
        <div>
          <p>Zalogowano jako <strong>{state.user.username}</strong>.</p>
          <button type="button" disabled={state.busy} onClick={() => { void controller.current?.logout() }}>
            {state.busy ? 'Wylogowywanie…' : 'Wyloguj'}
          </button>
        </div>
      ) : (
        <form className="auth-form" onSubmit={login}>
          <label>
            <span>Nazwa użytkownika</span>
            <input name="username" type="text" autoComplete="username" required disabled={state.busy} />
          </label>
          <label>
            <span>Hasło</span>
            <input name="password" type="password" autoComplete="current-password" required disabled={state.busy} />
          </label>
          <button type="submit" disabled={state.busy}>{state.busy ? 'Logowanie…' : 'Zaloguj'}</button>
          <p className="settings-note">Na zaufanym urządzeniu sesja pozostaje aktywna do 180 dni, z limitem 30 dni od ostatniej zapisanej aktywności.</p>
        </form>
      )}
      {state.error && (
        <div className="settings-error" role="alert">
          <span>{state.error}</span>
          <button type="button" disabled={state.busy} onClick={() => { void controller.current?.refresh() }}>Sprawdź sesję</button>
        </div>
      )}
    </section>
  )
}
