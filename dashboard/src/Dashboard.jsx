import { useEffect, useRef, useState } from 'react'
import { io } from 'socket.io-client'
import AdminPage from './AdminPage.jsx'
import { AiVerdict } from './alertUi.jsx'
import { ruleLabel } from './labels.js'
import { api, SERVER_URL } from './api.js'
import ReportsPage from './ReportsPage.jsx'

const OFFLINE_AFTER_MS = 10_000
const BANDWIDTH_WINDOW_MS = 5_000
// the server re-alerts a persisting violation every 60s, so this keeps the tile red while it lasts
const ALERT_HIGHLIGHT_MS = 70_000
const TOAST_MS = 6_000
const MAX_ALERTS = 200
// must match server/risk.py
const RISK_DECAY_PER_MIN = 2
const TOP_N = 3

function riskLevel(score) {
  if (score >= 60) return 'high'
  if (score >= 30) return 'medium'
  return 'low'
}

export default function Dashboard({ auth, onLogout }) {
  const canRunSessions = auth.role === 'admin' || auth.role === 'teacher'
  const [session, setSession] = useState(null)
  const [profiles, setProfiles] = useState([])
  const [view, setView] = useState('monitor')
  const onLogoutRef = useRef(onLogout)
  useEffect(() => {
    onLogoutRef.current = onLogout
  }, [onLogout])
  // device_id -> { device_id, hostname, os, image, lastHeartbeat }
  const [devices, setDevices] = useState({})
  const [focusedId, setFocusedId] = useState(null)
  const [now, setNow] = useState(() => Date.now())
  // recomputed every second from samplesRef
  const [stats, setStats] = useState({ kbps: 0, fps: {} })
  const [connected, setConnected] = useState(false)
  // newest first: { id, device_id, rule, severity, detail, profile, screenshot_at_time, ts }
  const [alerts, setAlerts] = useState([])
  const [toasts, setToasts] = useState([])
  const [evidence, setEvidence] = useState(null)
  // device_id -> { score, at }: score as reported by the server, at = browser time it arrived.
  // Decay is applied locally from `at`, so server/browser clock skew doesn't matter.
  const [risk, setRisk] = useState({})
  const [sortByRisk, setSortByRisk] = useState(true)
  // device_ids an invigilator has locked
  const [locked, setLocked] = useState([])
  // neutral notices: results of controls reported back by agents, and control errors
  const [notices, setNotices] = useState([])
  const canKill = auth.role === 'admin' || auth.role === 'teacher'

  function notify(text) {
    const id = `${Date.now()}-${Math.random()}`
    setNotices((prev) => [...prev, { id, text }])
    setTimeout(() => setNotices((prev) => prev.filter((n) => n.id !== id)), TOAST_MS)
  }

  async function sendControl(target, command, extra = {}) {
    try {
      await api('/control', { token: auth.token, method: 'POST', body: { target, command, ...extra } })
      return true
    } catch (err) {
      notify(`${command} failed: ${err.message}`)
      return false
    }
  }
  const socketRef = useRef(null)
  const focusedRef = useRef(null)
  // [receivedAtMs, bytes, device_id] for every screen frame received
  const samplesRef = useRef([])

  useEffect(() => {
    focusedRef.current = focusedId
  }, [focusedId])

  useEffect(() => {
    let refetching = false
    const knownIds = new Set()

    // authenticated GET; an expired/invalid login sends the user back to the login screen
    async function get(path) {
      try {
        return await api(path, { token: auth.token })
      } catch (err) {
        if (err.status === 401) onLogoutRef.current()
        throw err
      }
    }

    async function loadSession() {
      try {
        setSession((await get('/session')).session)
      } catch (err) {
        console.error('GET /session failed', err)
      }
    }

    async function loadProfiles() {
      try {
        setProfiles(await get('/profiles'))
      } catch (err) {
        console.error('GET /profiles failed', err)
      }
    }

    async function loadDevices() {
      if (refetching) return
      refetching = true
      try {
        const list = await get('/devices')
        for (const d of list) knownIds.add(d.device_id)
        setLocked(list.filter((d) => d.locked).map((d) => d.device_id))
        setDevices((prev) => {
          const next = { ...prev }
          for (const d of list) next[d.device_id] = { ...next[d.device_id], ...d }
          return next
        })
      } catch (err) {
        console.error('GET /devices failed', err)
      } finally {
        refetching = false
      }
    }

    async function loadAlerts() {
      try {
        setAlerts(await get(`/alerts?limit=${MAX_ALERTS}`))
      } catch (err) {
        console.error('GET /alerts failed', err)
      }
    }

    async function loadRisk() {
      try {
        const list = await get('/risk')
        const at = Date.now()
        const next = {}
        for (const r of list) next[r.device_id] = { score: r.score, at }
        setRisk(next)
        // a device can have a score but no longer be connected (e.g. agent lost):
        // it still needs a tile, shown offline
        setDevices((prev) => {
          const merged = { ...prev }
          for (const r of list) {
            knownIds.add(r.device_id)
            if (!merged[r.device_id]) merged[r.device_id] = { device_id: r.device_id }
          }
          return merged
        })
      } catch (err) {
        console.error('GET /risk failed', err)
      }
    }

    const socket = io(SERVER_URL, { auth: { role: 'dashboard', token: auth.token } })
    socketRef.current = socket

    // the server refuses the socket if the login is no longer valid; /me confirms and logs out
    socket.on('connect_error', () => get('/me').catch(() => {}))
    socket.on('locked', (ids) => setLocked(ids))
    // the vision model's verdict arrives a few seconds after the alert itself
    socket.on('alert_ai', (update) => {
      setAlerts((prev) => prev.map((a) => (a.id === update.id ? { ...a, ...update } : a)))
      setEvidence((e) => (e && e.id === update.id ? { ...e, ...update } : e))
    })
    socket.on('session', (s) => {
      setSession(s)
      // risk scores are per session on the server
      setRisk({})
      loadRisk()
    })

    socket.on('connect', () => {
      setConnected(true)
      loadSession()
      loadProfiles()
      loadDevices()
      loadAlerts()
      loadRisk()
      // a new socket means the server forgot our focus; ask again
      if (focusedRef.current) socket.emit('focus', { device_id: focusedRef.current, on: true })
    })
    socket.on('disconnect', () => setConnected(false))

    socket.on('event', (ev) => {
      const id = ev.device_id
      if (!knownIds.has(id)) {
        knownIds.add(id)
        loadDevices() // new device: fetch its hostname/os
      }
      setDevices((prev) => {
        const d = { device_id: id, ...prev[id] }
        if (ev.type === 'heartbeat') d.lastHeartbeat = Date.now()
        if (ev.type === 'screen') d.image = `data:image/jpeg;base64,${ev.data.image}`
        if (ev.type === 'window' && ev.data.kind === 'active') d.active = ev.data
        return { ...prev, [id]: d }
      })
      if (ev.type === 'screen') samplesRef.current.push([Date.now(), ev.data.image.length, id])
      if (ev.type === 'control' && ev.data.kind === 'control_result') {
        const id2 = `${Date.now()}-${Math.random()}`
        setNotices((prev) => [...prev, { id: id2, text: `${id} · ${ev.data.command}: ${ev.data.detail}` }])
        setTimeout(() => setNotices((prev) => prev.filter((n) => n.id !== id2)), TOAST_MS)
      }
      if (ev.type === 'alert') {
        const alert = { device_id: id, ...ev.data }
        setAlerts((prev) => [alert, ...prev.filter((a) => a.id !== alert.id)].slice(0, MAX_ALERTS))
        setToasts((prev) => [...prev, alert])
        setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== alert.id)), TOAST_MS)
        if (typeof ev.data.risk === 'number') {
          setRisk((prev) => ({ ...prev, [id]: { score: ev.data.risk, at: Date.now() } }))
        }
      }
    })

    const timer = setInterval(() => {
      const t = Date.now()
      const samples = samplesRef.current.filter(([at]) => t - at < BANDWIDTH_WINDOW_MS)
      samplesRef.current = samples
      const totalBytes = samples.reduce((sum, [, bytes]) => sum + bytes, 0)
      const fps = {}
      for (const [at, , id] of samples) if (t - at < 1000) fps[id] = (fps[id] || 0) + 1
      setStats({ kbps: totalBytes / 1024 / (BANDWIDTH_WINDOW_MS / 1000), fps })
      setNow(t)
    }, 1000)

    return () => {
      clearInterval(timer)
      socket.disconnect()
    }
  }, [auth.token])

  async function startSession(profile) {
    await api('/session/start', { token: auth.token, method: 'POST', body: { profile } })
  }

  async function endSession() {
    await api('/session/end', { token: auth.token, method: 'POST' })
  }

  function openFocus(id) {
    if (focusedId && focusedId !== id) socketRef.current?.emit('focus', { device_id: focusedId, on: false })
    setFocusedId(id)
    socketRef.current?.emit('focus', { device_id: id, on: true })
  }

  function closeFocus() {
    if (focusedId) socketRef.current?.emit('focus', { device_id: focusedId, on: false })
    setFocusedId(null)
  }

  useEffect(() => {
    if (!focusedId && !evidence) return
    const onKey = (e) => {
      if (e.key !== 'Escape') return
      if (evidence) setEvidence(null)
      else closeFocus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const riskOf = (id) => {
    const r = risk[id]
    return r ? Math.max(0, r.score - (RISK_DECAY_PER_MIN * (now - r.at)) / 60_000) : 0
  }
  const byName = (a, b) => a.device_id.localeCompare(b.device_id)
  const byRisk = (a, b) => riskOf(b.device_id) - riskOf(a.device_id) || byName(a, b)
  const list = Object.values(devices).sort(sortByRisk ? byRisk : byName)
  // top 3 by risk, whatever the current sort; a score of 0 is never highlighted
  const rankOf = {}
  Object.values(devices)
    .sort(byRisk)
    .filter((d) => riskOf(d.device_id) >= 0.5)
    .slice(0, TOP_N)
    .forEach((d, i) => (rankOf[d.device_id] = i + 1))
  const isOnline = (d) => d.lastHeartbeat && now - d.lastHeartbeat < OFFLINE_AFTER_MS
  const onlineCount = list.filter(isOnline).length
  const focused = focusedId ? devices[focusedId] : null
  const focusFps = stats.fps[focusedId] || 0
  const lastAlertAt = {}
  for (const a of alerts) lastAlertAt[a.device_id] = Math.max(lastAlertAt[a.device_id] || 0, a.ts * 1000)
  const isAlerted = (d) => now - (lastAlertAt[d.device_id] || 0) < ALERT_HIGHLIGHT_MS
  // device_id is the identity (defaults to the hostname on the agent)
  const nameOf = (id) => id

  return (
    <div className="flex h-screen flex-col print:block print:h-auto">
      <header className="flex shrink-0 items-center justify-between border-b border-neutral-900 px-10 py-5 print:hidden">
        <div className="flex items-center gap-8">
          <div className="flex items-center gap-3">
            <span className="text-lg font-semibold text-white">InvigilAI</span>
            <span
              className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-400' : 'bg-neutral-600'}`}
              title={connected ? 'Connected to server' : 'Disconnected from server'}
            />
          </div>
          <SessionControl
            session={session}
            canRun={canRunSessions}
            profiles={profiles}
            onStart={startSession}
            onEnd={endSession}
          />
          {session && <BroadcastMessage onSend={(text) => sendControl('all', 'message', { message: text })} />}
        </div>
        <div className="flex items-center gap-8 text-sm">
          {view === 'monitor' && (
            <>
              <button
                onClick={() => setSortByRisk((v) => !v)}
                className="flex items-center gap-2 text-neutral-500 hover:text-neutral-200"
                aria-pressed={sortByRisk}
              >
                <span className={`relative h-4 w-7 rounded-full transition ${sortByRisk ? 'bg-emerald-400' : 'bg-neutral-700'}`}>
                  <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-neutral-950 transition-all ${sortByRisk ? 'left-3.5' : 'left-0.5'}`} />
                </span>
                Sort by risk
              </button>
              <Stat label="Online" value={`${onlineCount} / ${list.length}`} />
              <Stat label="LAN bandwidth" value={`${stats.kbps.toFixed(1)} KB/s`} />
            </>
          )}
          {view !== 'monitor' && (
            <button onClick={() => setView('monitor')} className="rounded-lg px-3 py-1.5 text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200">
              Back to monitor
            </button>
          )}
          {view !== 'reports' && (
            <button onClick={() => setView('reports')} className="rounded-lg px-3 py-1.5 text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200">
              Reports
            </button>
          )}
          {auth.role === 'admin' && view !== 'admin' && (
            <button onClick={() => setView('admin')} className="rounded-lg px-3 py-1.5 text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200">
              Admin
            </button>
          )}
          <div className="flex items-center gap-4 text-neutral-500">
            <span>
              {auth.username} <span className="text-xs text-neutral-600">{auth.role}</span>
            </span>
            <button onClick={onLogout} className="rounded-lg px-3 py-1.5 hover:bg-neutral-900 hover:text-neutral-200">
              Sign out
            </button>
          </div>
        </div>
      </header>

      {view === 'admin' ? (
        <AdminPage token={auth.token} onAuthError={onLogout} />
      ) : view === 'reports' ? (
        <ReportsPage token={auth.token} onAuthError={onLogout} />
      ) : (
      <div className="flex min-h-0 flex-1">
        <main className="flex-1 overflow-y-auto p-10">
          {list.length === 0 ? (
            <p className="mt-32 text-center text-sm text-neutral-500">
              {session
              ? 'No devices connected yet. Start an enrolled agent to see it here.'
              : 'No exam running. Agents send nothing until a session starts.'}
            </p>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-8">
              {list.map((d) => (
                <Tile
                  key={d.device_id}
                  device={d}
                  online={isOnline(d)}
                  alerted={isAlerted(d)}
                  risk={riskOf(d.device_id)}
                  rank={rankOf[d.device_id]}
                  isLocked={locked.includes(d.device_id)}
                  onClick={() => openFocus(d.device_id)}
                />
              ))}
            </div>
          )}
        </main>

        <aside className="flex w-96 shrink-0 flex-col border-l border-neutral-900">
          <div className="flex items-baseline justify-between px-6 pt-8 pb-4">
            <h2 className="text-sm font-medium text-white">Alerts</h2>
            <span className="text-xs tabular-nums text-neutral-500">{alerts.length}</span>
          </div>
          <div className="flex-1 space-y-3 overflow-y-auto px-6 pb-8">
            {alerts.length === 0 ? (
              <p className="pt-10 text-center text-sm text-neutral-600">No alerts yet</p>
            ) : (
              alerts.map((a) => (
                <AlertCard key={a.id} alert={a} name={nameOf(a.device_id)} onClick={() => setEvidence(a)} />
              ))
            )}
          </div>
        </aside>
      </div>
      )}

      <div className="pointer-events-none fixed bottom-6 left-6 z-30 flex w-96 flex-col gap-3">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto rounded-2xl border border-red-500/40 bg-neutral-900 p-4 shadow-2xl">
            <div className="flex items-center gap-2 text-sm font-medium text-red-400">
              <span className="h-2 w-2 rounded-full bg-red-500" />
              {ruleLabel(t.rule)}
            </div>
            <p className="mt-1 text-sm text-neutral-300">
              {nameOf(t.device_id)} · {t.detail}
            </p>
          </div>
        ))}
        {notices.map((n) => (
          <div key={n.id} className="pointer-events-auto rounded-2xl border border-neutral-700 bg-neutral-900 p-4 text-sm text-neutral-200 shadow-2xl">
            {n.text}
          </div>
        ))}
      </div>

      {evidence && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-neutral-950/80 p-10 backdrop-blur-sm" onClick={() => setEvidence(null)}>
          <div className="w-full max-w-4xl rounded-2xl bg-neutral-900 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-5 flex items-start justify-between gap-6">
              <div className="space-y-1">
                <div className="flex items-center gap-3">
                  <SeverityPill severity={evidence.severity} />
                  <span className="font-medium text-white">{ruleLabel(evidence.rule)}</span>
                </div>
                <p className="text-sm text-neutral-400">
                  {nameOf(evidence.device_id)} · {evidence.detail} · {formatTime(evidence.ts)} · {evidence.profile}
                </p>
                {evidence.ai_status && evidence.ai_status !== 'skipped' && (
                  <div className="flex items-start gap-3 pt-2">
                    <AiVerdict alert={evidence} />
                    {evidence.ai_status === 'done' && <p className="text-sm text-neutral-400">{evidence.ai_reason}</p>}
                    {evidence.ai_status === 'error' && <p className="text-sm text-neutral-500">{evidence.ai_reason}</p>}
                  </div>
                )}
              </div>
              <button onClick={() => setEvidence(null)} className="rounded-lg px-3 py-1.5 text-sm text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200">
                Close
              </button>
            </div>
            <Screen image={evidence.screenshot_at_time && `data:image/jpeg;base64,${evidence.screenshot_at_time}`} empty="No screenshot captured" />
          </div>
        </div>
      )}

      {focused && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-neutral-950/80 p-10 backdrop-blur-sm" onClick={closeFocus}>
          <div className="w-full max-w-5xl rounded-2xl bg-neutral-900 p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-5 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Dot online={isOnline(focused)} />
                <span className="font-medium text-white">{focused.device_id}</span>
                <span className="text-sm text-neutral-500">{focused.os}</span>
                {locked.includes(focused.device_id) && <LockedChip />}
              </div>
              <div className="flex items-center gap-6 text-sm text-neutral-500">
                <span>{focusFps} fps</span>
                <button onClick={closeFocus} className="rounded-lg px-3 py-1.5 hover:bg-neutral-800 hover:text-neutral-200">
                  Close
                </button>
              </div>
            </div>
            <Screen image={focused.image} />
            {session && (
              <DeviceControls
                deviceId={focused.device_id}
                isLocked={locked.includes(focused.device_id)}
                canKill={canKill}
                onControl={sendControl}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function SessionControl({ session, canRun, profiles, onStart, onEnd }) {
  const [profile, setProfile] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const chosen = profile || profiles[0]?.name || ''

  async function run(action) {
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const button = 'rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50'
  return (
    <div className="flex items-center gap-3 text-sm">
      {session ? (
        <>
          <span className="flex items-center gap-2 rounded-full bg-emerald-400/10 px-3 py-1 text-emerald-300">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            Exam running · {session.profile}
          </span>
          <span className="text-xs text-neutral-500">
            since {new Date(session.started_at * 1000).toLocaleTimeString()} by {session.started_by}
          </span>
          {canRun && (
            <button disabled={busy} onClick={() => run(onEnd)} className={`${button} bg-neutral-800 text-neutral-200 hover:bg-neutral-700`}>
              End exam
            </button>
          )}
        </>
      ) : (
        <>
          <span className="text-neutral-500">No exam running</span>
          {canRun && (
            <>
              <select
                value={chosen}
                onChange={(e) => setProfile(e.target.value)}
                aria-label="Profile"
                className="rounded-lg bg-neutral-800 px-3 py-1.5 text-neutral-200 outline-none"
              >
                {profiles.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                disabled={busy || !chosen}
                onClick={() => run(() => onStart(chosen))}
                className={`${button} bg-emerald-400 text-neutral-950 hover:bg-emerald-300`}
              >
                Start exam
              </button>
            </>
          )}
        </>
      )}
      {error && <span className="text-xs text-neutral-300">{error}</span>}
    </div>
  )
}

function LockedChip() {
  return <span className="rounded-full bg-neutral-700 px-2.5 py-0.5 text-[11px] font-medium text-white">Locked</span>
}

const controlInput =
  'min-w-0 flex-1 rounded-lg bg-neutral-800 px-3 py-1.5 text-sm text-neutral-100 outline-none placeholder:text-neutral-500 focus:ring-2 focus:ring-emerald-400/60'
const controlButton = 'shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-40'

function DeviceControls({ deviceId, isLocked, canKill, onControl }) {
  const [lockText, setLockText] = useState('')
  const [message, setMessage] = useState('')
  const [proc, setProc] = useState('')

  return (
    <div className="mt-5 grid gap-3 md:grid-cols-3">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          onControl(deviceId, isLocked ? 'unlock' : 'lock', { message: lockText })
        }}
      >
        {!isLocked && <input value={lockText} onChange={(e) => setLockText(e.target.value)} placeholder="Lock message (optional)" className={controlInput} />}
        <button type="submit" className={`${controlButton} ${isLocked ? 'flex-1 bg-emerald-400 text-neutral-950 hover:bg-emerald-300' : 'bg-neutral-700 text-white hover:bg-neutral-600'}`}>
          {isLocked ? 'Unlock screen' : 'Lock'}
        </button>
      </form>
      <form
        className="flex gap-2"
        onSubmit={async (e) => {
          e.preventDefault()
          if (await onControl(deviceId, 'message', { message })) setMessage('')
        }}
      >
        <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Message to this student" className={controlInput} />
        <button type="submit" disabled={!message.trim()} className={`${controlButton} bg-neutral-700 text-white hover:bg-neutral-600`}>
          Send
        </button>
      </form>
      {canKill && (
        <form
          className="flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            if (await onControl(deviceId, 'kill', { name: proc })) setProc('')
          }}
        >
          <input value={proc} onChange={(e) => setProc(e.target.value)} placeholder="Process name, e.g. notepad.exe" className={controlInput} />
          <button type="submit" disabled={!proc.trim()} className={`${controlButton} bg-neutral-700 text-white hover:bg-neutral-600`}>
            Kill
          </button>
        </form>
      )}
    </div>
  )
}

function BroadcastMessage({ onSend }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="rounded-lg px-3 py-1.5 text-sm text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200">
        Message all
      </button>
    )
  }
  return (
    <form
      className="flex w-96 gap-2"
      onSubmit={async (e) => {
        e.preventDefault()
        if (await onSend(text)) {
          setText('')
          setOpen(false)
        }
      }}
    >
      <input autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Message to every student" className={controlInput} />
      <button type="submit" disabled={!text.trim()} className={`${controlButton} bg-emerald-400 text-neutral-950 hover:bg-emerald-300`}>
        Send
      </button>
      <button type="button" onClick={() => setOpen(false)} className={`${controlButton} text-neutral-500 hover:text-neutral-200`}>
        Cancel
      </button>
    </form>
  )
}

function Stat({ label, value }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-neutral-500">{label}</span>
      <span className="font-medium tabular-nums text-white">{value}</span>
    </div>
  )
}

function Dot({ online }) {
  return (
    <span
      className={`h-2.5 w-2.5 shrink-0 rounded-full ${online ? 'bg-emerald-400' : 'bg-neutral-600'}`}
      title={online ? 'Online' : 'Offline (no heartbeat for 10s)'}
    />
  )
}

function Screen({ image, empty = 'Waiting for frame…' }) {
  return image ? (
    <img src={image} alt="" className="aspect-[16/10] w-full rounded-xl bg-neutral-800 object-contain" />
  ) : (
    <div className="flex aspect-[16/10] w-full items-center justify-center rounded-xl bg-neutral-800 text-sm text-neutral-500">
      {empty}
    </div>
  )
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString()
}

function SeverityPill({ severity }) {
  const style = severity === 'high' ? 'bg-red-500/15 text-red-400' : 'bg-neutral-800 text-neutral-400'
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${style}`}>{severity}</span>
}

function AlertCard({ alert, name, onClick }) {
  return (
    <button onClick={onClick} className="flex w-full gap-3 rounded-2xl bg-neutral-900 p-3 text-left hover:bg-neutral-800/80">
      {alert.screenshot_at_time ? (
        <img src={`data:image/jpeg;base64,${alert.screenshot_at_time}`} alt="" className="h-14 w-24 shrink-0 rounded-lg bg-neutral-800 object-cover" />
      ) : (
        <div className="h-14 w-24 shrink-0 rounded-lg bg-neutral-800" />
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-red-400">{ruleLabel(alert.rule)}</span>
          <span className="ml-auto shrink-0 text-[11px] tabular-nums text-neutral-500">{formatTime(alert.ts)}</span>
        </div>
        <p className="truncate text-xs text-neutral-300">{name}</p>
        <p className="truncate text-xs text-neutral-500">{alert.detail}</p>
        <AiVerdict alert={alert} />
      </div>
    </button>
  )
}

function Activity({ active }) {
  const app = active?.process?.replace(/\.exe$/i, '')
  return (
    <div className="mt-1.5 flex items-center gap-2 pr-1 pl-6.5 text-xs text-neutral-500" title={active?.title || ''}>
      <span className="truncate">{app || '—'}</span>
      {active?.domain && (
        <>
          <span className="text-neutral-700">·</span>
          <span className="truncate text-emerald-400/80">{active.domain}</span>
        </>
      )}
    </div>
  )
}

function RiskBadge({ score }) {
  const lvl = riskLevel(score)
  const style = {
    high: 'bg-red-500/15 text-red-400',
    medium: 'bg-neutral-700 text-neutral-100',
    low: 'bg-neutral-800 text-neutral-400',
  }[lvl]
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums ${style}`} title="Integrity Risk Score">
      {Math.round(score)} · {lvl}
    </span>
  )
}

function Tile({ device, online, alerted, risk, rank, isLocked, onClick }) {
  const ring = alerted
    ? 'ring-2 ring-red-500'
    : rank
      ? 'ring-1 ring-white/30 hover:ring-2 hover:ring-emerald-400/50'
      : 'hover:ring-2 hover:ring-emerald-400/50'
  return (
    <button
      onClick={onClick}
      className={`group rounded-2xl p-4 text-left transition ${rank ? 'bg-neutral-800/70' : 'bg-neutral-900'} ${ring} ${online ? '' : 'opacity-60'}`}
    >
      <div className="relative">
        <Screen image={device.image} />
        {rank && (
          <span className="absolute top-2 left-2 rounded-full bg-neutral-950/85 px-2.5 py-1 text-[11px] font-medium text-white">
            Watch #{rank}
          </span>
        )}
        {isLocked && (
          <span className="absolute top-2 right-2">
            <LockedChip />
          </span>
        )}
      </div>
      <div className="mt-4 flex items-center gap-3 px-1">
        <Dot online={online} />
        <span
          className="truncate text-sm font-medium text-neutral-200"
          title={device.hostname && device.hostname !== device.device_id ? `host: ${device.hostname}` : undefined}
        >
          {device.device_id}
        </span>
        <span className="ml-auto flex items-center gap-3">
          <RiskBadge score={risk} />
          <span className="text-xs text-neutral-500">{online ? 'Online' : 'Offline'}</span>
        </span>
      </div>
      <Activity active={device.active} />
    </button>
  )
}
