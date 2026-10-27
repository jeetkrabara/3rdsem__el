import { statusOf } from '../api.js'

export default function StatusPill({ status }) {
  const s = statusOf(status)
  return <span className="pill status" style={{ background: s.color }}>{s.label}</span>
}
