import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ha, STATUS } from '../api.js'
import LakesMap from '../components/LakesMap.jsx'
import LiveBadge from '../components/LiveBadge.jsx'
import StatusPill from '../components/StatusPill.jsx'
import { EVENT_STYLE } from '../components/ChangeEvents.jsx'

const POLL_MS = 5 * 60 * 1000     // check for newly measured images every 5 minutes

export default function Dashboard() {
  const [summary, setSummary] = useState(null)
  const [geo, setGeo] = useState(null)
  const [ranking, setRanking] = useState([])
  const [alerts, setAlerts] = useState([])
  const [events, setEvents] = useState(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState(null)
  const shownImage = useRef(null)

  const load = useCallback(() => {
    Promise.all([api.summary(), api.geojson(), api.priority(), api.alerts()])
      .then(([s, g, p, a]) => {
        setSummary(s); setGeo(g); setRanking(p); setAlerts(a); setError(null)
        shownImage.current = s.newest_image
      })
    api.cityEvents(8).then(setEvents, () => setEvents(null))
      .catch((err) => setError(`Data unavailable: ${err.message}. Are the backend (8000) and AI service (8001) running?`))
  }, [])

  useEffect(() => { load() }, [load])

  // live: poll the summary; reload everything when a newer image has been measured
  useEffect(() => {
    const timer = setInterval(() => {
      api.summary().then((s) => {
        if (s.newest_image !== shownImage.current) load()
        else setSummary(s)
      }, () => {})
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  const lakes = useMemo(() => (geo?.features || []).map((f) => f.properties).filter((p) => p.id), [geo])
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return lakes.filter((l) => (l.name || '').toLowerCase().includes(q)).slice(0, 8)
  }, [query, lakes])

  const legend = ['persistent_degradation', 'below_seasonal_normal', 'normal_for_season', 'above_seasonal_normal', 'normal_too_small', 'no_measurements']

  return (
    <div className="stack">
      <div className="row">
        <div>
          <h1>Bengaluru lake health</h1>
          <p className="muted" style={{ margin: '0.3rem 0 0' }}>
            {summary?.lakes_total ?? ''} well-known lakes · open water measured on real Sentinel-2 images and compared with each lake's normal for the season.
          </p>
          {summary && <div style={{ marginTop: '0.5rem' }}><LiveBadge summary={summary} /></div>}
        </div>
        <span className="spacer" />
        <div style={{ position: 'relative', minWidth: 260 }}>
          <input className="search" placeholder="Find a lake…" value={query} onChange={(e) => setQuery(e.target.value)} />
          {matches.length > 0 && (
            <div className="card" style={{ position: 'absolute', zIndex: 1000, width: '100%', padding: '0.4rem', marginTop: 4 }}>
              {matches.map((l) => (
                <div key={l.id} style={{ padding: '0.3rem 0.4rem' }}>
                  <Link to={`/lake/${l.id}`}>{l.name}</Link>
                  {l.status && <span className="muted small"> · {STATUS[l.status]?.label || l.status}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {error && <div className="banner danger">{error}</div>}

      {summary && (
        <div className="kpis">
          <Kpi label="Lakes measured" value={`${summary.lakes_measured} / ${summary.lakes_total}`} sub="others are being backfilled" />
          <Kpi label="Below seasonal normal" value={summary.lakes_below_normal} sub="≥15% less open water than usual" color="#b42318" />
          <Kpi label="Water replaced by plants" value={summary.lakes_open_water_replaced_by_plants} sub="weeds / marsh over the lake" color="#28a03c" />
          <Kpi label="Newest image" value={summary.newest_image} sub={`processed ${summary.data_processed}`} />
        </div>
      )}

      <div className="grid dash">
        <div className="card">
          <h2>Bengaluru's well-known lakes</h2>
          <LakesMap data={geo} />
          <div className="legend">
            {legend.map((k) => (
              <span key={k}><span className="swatch" style={{ background: STATUS[k].color }} />{STATUS[k].label}</span>
            ))}
          </div>
          <p className="muted small" style={{ marginBottom: 0 }}>Click a lake to open its satellite history. Outlines: © OpenStreetMap contributors.</p>
        </div>

        <div className="card">
          <h2>Restoration priority</h2>
          <div className="table-wrap" style={{ maxHeight: 560, overflowY: 'auto' }}>
            <table>
              <thead><tr><th>#</th><th>Lake</th><th className="num">Open water</th><th className="num">Score</th></tr></thead>
              <tbody>
                {ranking.map((r) => (
                  <tr key={r.waterbody_id}>
                    <td className="muted">{r.rank}</td>
                    <td>
                      <Link to={`/lake/${r.waterbody_id}`}><strong>{r.name}</strong></Link>
                      <div><StatusPill status={r.status} /></div>
                    </td>
                    <td className="num small">{ha(r.open_water_ha)}<div className="muted">{r.date}</div></td>
                    <td className="num"><strong>{r.priority_score}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {ranking[0]?.note && <p className="muted small" style={{ marginBottom: 0 }}>{ranking[0].note}</p>}
        </div>
      </div>

      {events?.events?.length > 0 && (
        <div className="card">
          <h2>Latest changes the rain cannot explain</h2>
          <p className="muted small" style={{ marginTop: 0 }}>
            {events.total} events across {events.lakes.length} lakes since 2017 · detector validated against documented events
            ({events.validation.detected} of {events.validation.documented} found; misses were below that lake's detectable size).
          </p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Lake</th><th>When</th><th>What</th><th className="num">Change</th><th className="num">Rain</th></tr></thead>
              <tbody>
                {events.events.map((e) => (
                  <tr key={e.lake_id + e.start}>
                    <td>{e.waterbody_id ? <Link to={`/lake/${e.waterbody_id}?tab=events`}><strong>{e.name}</strong></Link> : e.name}
                      {e.also_at.length > 0 && <div className="muted small">also: {e.also_at.join(', ')}</div>}</td>
                    <td className="small">{e.start} → {e.end}</td>
                    <td><span className="pill" style={{ background: EVENT_STYLE[e.type].color }}>{EVENT_STYLE[e.type].short}</span></td>
                    <td className="num">{e.change_ha > 0 ? '+' : ''}{e.change_ha} ha</td>
                    <td className="num small">{e.rain_mm} mm</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Alerts</h2>
        {alerts.length === 0 ? <p className="muted small">No lake is currently below its seasonal normal.</p> : (
          <div className="grid grid-3">
            {alerts.map((a) => (
              <div key={a.id} className={`banner ${a.severity === 'high' ? 'danger' : 'warn'}`}>
                <Link to={`/lake/${a.waterbody_id}`}><strong>{a.severity === 'high' ? 'High' : 'Watch'}</strong></Link> · {a.message}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Kpi({ label, value, sub, color }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>{value ?? '—'}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}
