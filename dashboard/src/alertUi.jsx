// The vision model's second opinion on an alert's screenshot.
export function AiVerdict({ alert, long = false }) {
  const status = alert.ai_status
  if (!status || status === 'skipped') return null
  const base = 'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium'
  if (status === 'pending') return <span className={`${base} bg-neutral-800 text-neutral-400 print:bg-transparent print:text-neutral-600`}>AI checking…</span>
  if (status === 'error') {
    return (
      <span className={`${base} bg-neutral-800 text-neutral-400 print:bg-transparent print:text-neutral-600`} title={alert.ai_reason || ''}>
        AI check failed{long && alert.ai_reason ? `: ${alert.ai_reason}` : ''}
      </span>
    )
  }
  const pct = alert.ai_confidence == null ? '' : ` · ${Math.round(alert.ai_confidence * 100)}%`
  const label = alert.ai_violation ? `AI confirmed${pct}` : `AI: likely false alarm${pct}`
  const style = alert.ai_violation
    ? 'bg-red-500/15 text-red-400 print:bg-transparent print:text-red-700'
    : 'bg-neutral-800 text-neutral-300 print:bg-transparent print:text-neutral-700'
  return (
    <span className={`${base} ${style}`} title={alert.ai_reason || ''}>
      {label}
    </span>
  )
}
