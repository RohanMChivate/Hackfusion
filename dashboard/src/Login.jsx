import { useState } from 'react'
import { api } from './api.js'

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      onLogin(await api('/auth/login', { method: 'POST', body: { username, password } }))
    } catch (err) {
      setError(err.status === 401 ? 'Wrong username or password' : `Login failed: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  const input =
    'w-full rounded-xl bg-neutral-800 px-4 py-3 text-sm outline-none placeholder:text-neutral-500 focus:ring-2 focus:ring-emerald-400/60'

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-6 rounded-2xl bg-neutral-900 p-10">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-white">InvigilAI</h1>
          <p className="text-sm text-neutral-500">Sign in to monitor the exam</p>
        </div>
        <div className="space-y-3">
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Username" autoComplete="username" className={input} />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoComplete="current-password"
            className={input}
          />
        </div>
        {error && <p className="text-sm text-neutral-300">{error}</p>}
        <button
          type="submit"
          disabled={busy || !username || !password}
          className="w-full rounded-xl bg-emerald-400 py-3 text-sm font-medium text-neutral-950 hover:bg-emerald-300 disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
