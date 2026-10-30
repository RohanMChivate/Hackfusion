import { useEffect, useState } from 'react'
import { AiVerdict } from './alertUi.jsx'
import { api } from './api.js'
import { ruleLabel } from './labels.js'

function riskLevel(score) {
  if (score >= 60) return 'high'
  if (score >= 30) return 'medium'
  return 'low'
}

const dateTime = (ts) => new Date(ts * 1000).toLocaleString()
const time = (ts) => new Date(ts * 1000).toLocaleTimeString()

// GET with login; 401 sends the user back to the login screen
function useApi(path, token, onAuthError) {
  const [state, setState] = useState({ data: null, error: '', loading: Boolean(path) })
  useEffect(() => {
    if (!path) return
    let cancelled = false
    api(path, { token })
      .then((data) => !cancelled && setState({ data, error: '', loading: false }))
      .catch((err) => {
        if (cancelled) return
        if (err.status === 401) onAuthError()
        setState({ data: null, error: err.message, loading: false })
      })
    return () => {
      cancelled = true
    }
  }, [path, token, onAuthError])
  return path ? state : { data: null, error: '', loading: false }
}

export default function ReportsPage({ token, onAuthError }) {
  const [sessionId, setSessionId] = useState(null)
  const [deviceId, setDeviceId] = useState(null)
  const sessions = useApi('/sessions', token, onAuthError)
  const students = useApi(sessionId ? `/sessions/${sessionId}/students` : null, token, onAuthError)
  const report = useApi(sessionId && deviceId ? `/sessions/${sessionId}/students/${encodeURIComponent(deviceId)}/report` : null, token, onAuthError)

  if (deviceId) {
    return (
      <div className="flex-1 overflow-y-auto p-10 print:overflow-visible print:p-0">
        <div className="mx-auto max-w-4xl">
          <div className="mb-6 flex items-center justify-between print:hidden">
            <button onClick={() => setDeviceId(null)} className="rounded-lg px-3 py-1.5 text-sm text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200">
              ← All students
            </button>
            <button
              onClick={() => window.print()}
              disabled={!report.data}
              className="rounded-xl bg-emerald-400 px-4 py-2 text-sm font-medium text-neutral-950 hover:bg-emerald-300 disabled:opacity-40"
            >
              Print / save as PDF
            </button>
          </div>
          {report.loading && <p className="text-sm text-neutral-500">Loading report… (the AI summary can take a few seconds the first time)</p>}
          {report.error && <p className="text-sm text-neutral-300">{report.error}</p>}
          {report.data && <Report r={report.data} />}
        </div>
      </div>
    )
  }

  const ended = (sessions.data || []).filter((s) => s.ended_at)
  return (
    <div className="flex-1 overflow-y-auto p-10">
      <div className="mx-auto grid max-w-5xl gap-8 md:grid-cols-[18rem_1fr]">
        <section className="rounded-2xl bg-neutral-900 p-5">
          <h2 className="mb-4 text-sm font-medium text-white">Ended sessions</h2>
          {sessions.error && <p className="text-sm text-neutral-300">{sessions.error}</p>}
          {!sessions.loading && ended.length === 0 && <p className="text-sm text-neutral-600">No ended sessions yet</p>}
          <div className="space-y-2">
            {ended.map((s) => (
              <button
                key={s.id}
                onClick={() => setSessionId(s.id)}
                className={`w-full rounded-xl px-4 py-3 text-left text-sm ${s.id === sessionId ? 'bg-neutral-800 text-white' : 'text-neutral-300 hover:bg-neutral-800/60'}`}
              >
                <div className="font-medium">
                  #{s.id} · {s.profile}
                </div>
                <div className="mt-0.5 text-xs text-neutral-500">{dateTime(s.started_at)}</div>
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-2xl bg-neutral-900 p-5">
          <h2 className="mb-4 text-sm font-medium text-white">{sessionId ? `Students in session #${sessionId}` : 'Pick a session'}</h2>
          {students.error && <p className="text-sm text-neutral-300">{students.error}</p>}
          {students.data && students.data.length === 0 && <p className="text-sm text-neutral-600">No devices took part</p>}
          <div className="space-y-2">
            {(students.data || []).map((s) => (
              <button
                key={s.device_id}
                onClick={() => setDeviceId(s.device_id)}
                className="flex w-full items-center gap-4 rounded-xl px-4 py-3 text-left text-sm text-neutral-200 hover:bg-neutral-800/60"
              >
                <span className="font-medium">{s.device_id}</span>
                <span className="text-xs text-neutral-500">{s.alert_count} alerts</span>
                <span className="ml-auto">
                  <RiskBadge score={s.final_risk} />
                </span>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}

function RiskBadge({ score }) {
  const lvl = riskLevel(score)
  const style = {
    high: 'bg-red-500/15 text-red-400 print:text-red-700',
    medium: 'bg-neutral-700 text-neutral-100 print:text-neutral-900',
    low: 'bg-neutral-800 text-neutral-400 print:text-neutral-700',
  }[lvl]
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium tabular-nums print:border print:border-neutral-400 print:bg-transparent ${style}`}>
      {Math.round(score)} · {lvl}
    </span>
  )
}

function Report({ r }) {
  const s = r.session
  const [generatedAt] = useState(() => new Date().toLocaleString())
  return (
    <article className="space-y-8 rounded-2xl bg-neutral-900 p-8 print:rounded-none print:bg-white print:p-0 print:text-black">
      <header className="space-y-2">
        <p className="text-xs tracking-wide text-neutral-500 uppercase print:text-neutral-600">Exam integrity report</p>
        <h1 className="text-2xl font-semibold text-white print:text-black">{r.device_id}</h1>
        <p className="text-sm text-neutral-400 print:text-neutral-700">
          Session #{s.id} · {s.profile} · {dateTime(s.started_at)} – {time(s.ended_at)} · started by {s.started_by}
        </p>
      </header>

      <section className="flex items-center gap-4">
        <span className="text-sm text-neutral-400 print:text-neutral-700">Final risk score</span>
        <RiskBadge score={r.final_risk} />
        <span className="text-sm text-neutral-500 print:text-neutral-700">{r.alerts.length} alerts</span>
      </section>

      <section className="rounded-xl bg-neutral-800/60 p-5 print:border print:border-neutral-300 print:bg-transparent">
        <h2 className="mb-2 text-xs tracking-wide text-neutral-500 uppercase print:text-neutral-600">Summary (written by AI)</h2>
        {r.summary ? (
          <div className="space-y-1 text-sm leading-relaxed text-neutral-100 print:text-black">
            {r.summary.split('\n').map((line, i) => (
              <p key={i}>{line}</p>
            ))}
          </div>
        ) : (
          <p className="text-sm text-neutral-500">No summary: {r.summary_error}</p>
        )}
      </section>

      <section>
        <h2 className="mb-4 text-xs tracking-wide text-neutral-500 uppercase print:text-neutral-600">Timeline</h2>
        {r.alerts.length === 0 && <p className="text-sm text-neutral-500">No alerts in this session.</p>}
        <ol className="space-y-6">
          {r.alerts.map((a) => (
            <li key={a.id} className="grid gap-4 border-t border-neutral-800 pt-5 break-inside-avoid md:grid-cols-[1fr_20rem] print:grid-cols-[1fr_16rem] print:border-neutral-300">
              <div className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm tabular-nums text-neutral-500 print:text-neutral-700">{time(a.ts)}</span>
                  <span className="text-sm font-medium text-red-400 print:text-red-700">{ruleLabel(a.rule)}</span>
                  <span className="text-xs text-neutral-500 uppercase print:text-neutral-600">{a.severity}</span>
                </div>
                <p className="text-sm text-neutral-200 print:text-black">{a.detail}</p>
                <AiVerdict alert={a} long />
                {a.ai_status === 'done' && <p className="text-xs text-neutral-400 print:text-neutral-700">{a.ai_reason}</p>}
              </div>
              {a.screenshot_at_time ? (
                <img src={`data:image/jpeg;base64,${a.screenshot_at_time}`} alt={`Screen at ${time(a.ts)}`} className="w-full rounded-lg print:rounded-none print:border print:border-neutral-300" />
              ) : (
                <div className="flex aspect-[16/10] items-center justify-center rounded-lg bg-neutral-800 text-xs text-neutral-500 print:border print:border-neutral-300 print:bg-transparent">
                  No screenshot
                </div>
              )}
            </li>
          ))}
        </ol>
      </section>

      <footer className="text-xs text-neutral-600 print:text-neutral-600">Generated {generatedAt} by InvigilAI</footer>
    </article>
  )
}
