import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'

const ACTION_LABELS = {
  login: 'Signed in',
  login_failed: 'Failed sign-in',
  view_screen: 'Viewed screen',
  session_start: 'Started exam session',
  session_end: 'Ended exam session',
  profile_update: 'Changed profile',
  enrollment_token_create: 'Created enrollment token',
  enroll: 'Device enrolled',
  enroll_rejected: 'Enrollment rejected',
  control_lock: 'Locked screen',
  control_unlock: 'Unlocked screen',
  control_message: 'Sent message',
  control_kill: 'Killed process',
  control_result: 'Agent reported',
}

export default function AdminPage({ token, onAuthError }) {
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')
  const [newToken, setNewToken] = useState(null)

  const load = useCallback(async () => {
    try {
      setRows(await api('/audit?limit=500', { token }))
      setError('')
    } catch (err) {
      if (err.status === 401) onAuthError()
      else setError(err.message)
    }
  }, [token, onAuthError])

  useEffect(() => {
    let cancelled = false
    api('/audit?limit=500', { token })
      .then((data) => !cancelled && setRows(data))
      .catch((err) => {
        if (cancelled) return
        if (err.status === 401) onAuthError()
        else setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [token, onAuthError])

  async function createToken() {
    try {
      setNewToken(await api('/enrollment-tokens', { token, method: 'POST' }))
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-10">
      <div className="mx-auto max-w-5xl space-y-8">
        <section className="rounded-2xl bg-neutral-900 p-6">
          <div className="flex items-center justify-between gap-6">
            <div>
              <h2 className="text-sm font-medium text-white">Device enrollment</h2>
              <p className="mt-1 text-sm text-neutral-500">
                One-time token for a new lab PC. Start its agent with <code className="text-neutral-300">ENROLL_TOKEN=&lt;token&gt;</code>.
              </p>
            </div>
            <button onClick={createToken} className="shrink-0 rounded-xl bg-emerald-400 px-4 py-2 text-sm font-medium text-neutral-950 hover:bg-emerald-300">
              New token
            </button>
          </div>
          {newToken && (
            <p className="mt-4 rounded-xl bg-neutral-800 px-4 py-3 font-mono text-sm text-neutral-100 select-all">
              {newToken.token}
              <span className="ml-3 font-sans text-xs text-neutral-500">token #{newToken.id} · shown once</span>
            </p>
          )}
        </section>

        <section className="rounded-2xl bg-neutral-900 p-6">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-sm font-medium text-white">Audit log</h2>
            <button onClick={load} className="rounded-lg px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200">
              Refresh
            </button>
          </div>
          {error && <p className="mb-4 text-sm text-neutral-300">{error}</p>}
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-neutral-500">
              <tr>
                <th className="py-2 pr-4 font-normal">Time</th>
                <th className="py-2 pr-4 font-normal">Who</th>
                <th className="py-2 pr-4 font-normal">Action</th>
                <th className="py-2 pr-4 font-normal">Target</th>
                <th className="py-2 font-normal">Detail</th>
              </tr>
            </thead>
            <tbody className="text-neutral-300">
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-neutral-800/80 align-top">
                  <td className="py-2.5 pr-4 whitespace-nowrap tabular-nums text-neutral-500">
                    {new Date(r.ts * 1000).toLocaleString()}
                  </td>
                  <td className="py-2.5 pr-4 whitespace-nowrap">
                    {r.actor} <span className="text-xs text-neutral-500">{r.role}</span>
                  </td>
                  <td className="py-2.5 pr-4 whitespace-nowrap">{ACTION_LABELS[r.action] || r.action}</td>
                  <td className="py-2.5 pr-4 whitespace-nowrap">{r.target || '—'}</td>
                  <td className="py-2.5 text-xs break-all text-neutral-500">{r.detail ? JSON.stringify(r.detail) : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && !error && <p className="py-6 text-center text-sm text-neutral-600">No entries</p>}
        </section>
      </div>
    </div>
  )
}
