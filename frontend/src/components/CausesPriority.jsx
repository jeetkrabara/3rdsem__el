import { api } from '../api.js'

const WEIGHTS = { severity: 0.4, persistence: 0.25, ecological_importance: 0.2, population_impact: 0.15 }
const LABELS = {
  severity: 'Severity — how far below normal',
  persistence: 'Persistence — share of recent dates below normal',
  ecological_importance: 'Ecological importance — size proxy (outline / 300 ha)',
  population_impact: 'Population impact — placeholder',
}
const CONF = { medium: '#dc6803', low: '#98a2b3', unchecked: '#98a2b3', 'n/a': '#98a2b3' }

export default function CausesPriority({ id, a }) {
  const hyps = a.cause_hypotheses?.hypotheses || []
  const p = a.priority
  return (
    <div className="grid grid-2">
      <div className="card">
        <h2>Possible causes</h2>
        {hyps.map((h) => (
          <div key={h.cause} className="banner" style={{ background: 'var(--surface-2)', marginBottom: '0.6rem' }}>
            <div className="row"><strong>{h.cause}</strong><span className="spacer" />
              <span className="pill" style={{ background: CONF[h.confidence] || '#98a2b3' }}>{h.confidence}</span></div>
            <div className="small muted" style={{ marginTop: 4 }}>{h.evidence}</div>
          </div>
        ))}
        <p className="muted small" style={{ marginBottom: 0 }}>{a.cause_hypotheses?.note}</p>
      </div>

      <div className="card stack">
        <h2 style={{ margin: 0 }}>Restoration priority</h2>
        {p ? (
          <>
            <div className="row" style={{ alignItems: 'baseline' }}>
              <span style={{ fontSize: '2.2rem', fontWeight: 700 }}>#{p.rank}</span>
              <span className="muted">of {a.lakes_ranked} measured lakes · score {p.priority_score} / 100</span>
            </div>
            <table>
              <tbody>
                {Object.keys(WEIGHTS).map((k) => (
                  <tr key={k}>
                    <td className="small">{LABELS[k]}<div className="muted">weight {WEIGHTS[k] * 100}%</div></td>
                    <td style={{ width: '35%' }}><div className="bar"><span style={{ width: `${p[k] ?? 0}%` }} /></div></td>
                    <td className="num">{p[k] == null ? '—' : Math.round(p[k])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small" style={{ margin: 0 }}>{a.priority_note}</p>
          </>
        ) : <p className="muted small">Not ranked: the lake has no judged measurement (too small, or not enough history).</p>}
        <div className="row"><a className="btn" href={api.reportUrl(id)}>Download evidence report (PDF)</a></div>
      </div>
    </div>
  )
}
