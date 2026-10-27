// "Live" indicator from the live updater's status (ai_agent/scripts/live_update.py).
function ago(iso) {
  if (!iso) return 'never'
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const h = Math.round(mins / 60)
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`
}

export default function LiveBadge({ summary }) {
  const live = summary.live || {}
  const on = live.running && live.state !== 'error'
  const color = live.state === 'error' ? '#b42318' : on ? '#15803d' : '#98a2b3'
  return (
    <span className="row small" style={{ gap: '0.45rem' }}>
      <span className="pill" style={{ background: color }}>
        {live.state === 'error' ? 'Update error' : on ? (live.state === 'idle' ? 'Live' : 'Updating…') : 'Not live'}
      </span>
      <span className="muted">
        Newest image <strong>{summary.newest_image}</strong>
        {' · '}satellite checked {ago(live.last_check)}
        {on && live.next_check && <> · next check {new Date(live.next_check).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</>}
        {!live.last_check && ' · run ai_agent/scripts/live_update.py to keep this current'}
        {live.state === 'error' && <> · {live.error}</>}
      </span>
    </span>
  )
}
