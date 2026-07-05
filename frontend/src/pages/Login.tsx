import { FormEvent, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { login } from '../api/auth'
import { Button, Field, inputCls } from '../components/Modal'

export function Login() {
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: { pathname: string } } }
  const dest = location.state?.from?.pathname ?? '/'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setErr(null); setBusy(true)
    try {
      await login(email, password)
      await refresh()
      navigate(dest, { replace: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <form onSubmit={onSubmit} className="w-full max-w-sm rounded-lg bg-white p-6 shadow-md">
        <div className="mb-4 flex items-center gap-3">
          <img src="/icon.svg" alt="Cante" className="h-10 w-10" />
          <div>
            <div className="text-2xl font-bold text-brand-700">Cante</div>
            <div className="text-sm text-gray-500">Sign in to the backoffice</div>
          </div>
        </div>
        <div className="space-y-3">
          <Field label="Email">
            <input className={inputCls} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@example.com" />
          </Field>
          <Field label="Password">
            <input className={inputCls} type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </div>
        {err && <div className="mt-3 rounded-md bg-red-50 p-2 text-xs text-red-700">{err}</div>}
        <div className="mt-4">
          <Button type="submit" disabled={busy} className="w-full">{busy ? 'Signing in…' : 'Sign in'}</Button>
        </div>
      </form>
    </div>
  )
}
