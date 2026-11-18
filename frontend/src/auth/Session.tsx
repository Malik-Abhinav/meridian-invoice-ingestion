import { createContext, useContext, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiClient, ApiError } from '../api/client'

type SessionValue = { token: string | null; client: ApiClient; signIn: (value: string) => Promise<void>; signOut: () => void; message: string | null }
const SessionContext = createContext<SessionValue | null>(null)
function clearPostingKeys() {
  try { for (const key of Object.keys(sessionStorage)) if (key.startsWith('meridian.posting.')) sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const queries = useQueryClient()
  const signOut = () => { setToken(null); queries.clear(); clearPostingKeys(); setMessage(null) }
  const unauthorized = () => { setToken(null); queries.clear(); clearPostingKeys(); setMessage('Your staff session was rejected or expired. Enter a valid token to continue.') }
  const client = useMemo(() => new ApiClient(() => token, unauthorized), [token, queries])

  async function signIn(value: string) {
    const candidate = value.trim()
    if (!candidate) throw new ApiError(401, 'authentication_required', 'Enter a staff token.')
    const probe = new ApiClient(() => candidate, () => undefined)
    await probe.invoices({ limit: 1, offset: 0 })
    queries.clear()
    setMessage(null)
    setToken(candidate)
  }

  return <SessionContext.Provider value={{ token, client, signIn, signOut, message }}>{children}</SessionContext.Provider>
}

export function useSession() {
  const session = useContext(SessionContext)
  if (!session) throw new Error('SessionProvider is required')
  return session
}

export function TokenGate() {
  const { signIn, message } = useSession()
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null)
    try { await signIn(value); setValue('') }
    catch (cause) { setError(cause instanceof ApiError ? `${cause.message}${cause.correlationId ? ` · Reference ${cause.correlationId}` : ''}` : 'Could not connect to the API.') }
    finally { setBusy(false) }
  }
  return <main className="auth-page"><section className="auth-card" aria-labelledby="auth-title">
    <div className="brand-mark">M</div><p className="eyebrow">Meridian operations</p>
    <h1 id="auth-title">Staff review, with a clear trail.</h1>
    <p className="muted">Enter the local development staff token configured in the FastAPI service. This assessment session holds it in memory only and clears it on sign out or refresh.</p>
    {message && <div className="notice warning" role="alert">{message}</div>}
    <form onSubmit={submit}><label htmlFor="staff-token">Staff bearer token</label>
      <input id="staff-token" type="password" autoComplete="off" value={value} onChange={event => setValue(event.target.value)} required autoFocus />
      {error && <p className="field-error" role="alert">{error}</p>}
      <button className="button primary full" type="submit" disabled={busy}>{busy ? 'Verifying…' : 'Enter workspace'}</button>
    </form><p className="small muted">Production identity verification belongs to the deployment, outside this dashboard.</p>
  </section></main>
}
