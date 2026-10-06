import type { FormEvent } from 'react'
import { createAuthController, type AuthState } from './auth'

type AuthPanelProps = {
  state: AuthState
  controller: ReturnType<typeof createAuthController> | null
}

export default function AuthPanel({ state, controller }: AuthPanelProps) {
  const login = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    const username = String(fields.get('username') ?? '')
    const password = String(fields.get('password') ?? '')
    form.reset()
    void controller?.login(username, password)
  }

  return (
    <section className="settings-section" aria-labelledby="account-heading">
      <h3 id="account-heading">Konto</h3>
      {state.loading ? <p>Sprawdzanie sesji…</p> : state.user ? (
        <div>
          <p>Zalogowano jako <strong>{state.user.username}</strong>.</p>
          <button type="button" disabled={state.busy} onClick={() => { void controller?.logout() }}>
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
          <button type="button" disabled={state.busy} onClick={() => { void controller?.refresh() }}>Sprawdź sesję</button>
        </div>
      )}
    </section>
  )
}
