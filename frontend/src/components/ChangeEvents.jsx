import { Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ha } from '../api.js'
import { useRender } from '../renderQueue.js'

export const EVENT_STYLE = {
  water_removed: { color: '#b42318', short: 'Water removed' },
  plants_covered_water: { color: '#28a03c', short: 'Plants covered water' },
  plants_cleared: { color: '#7a5af8', short: 'Plants cleared' },
  inflow_without_rain: { color: '#0b6bcb', short: 'Inflow without rain' },
}

export default function ChangeEvents({ id, data }) {
  if (!data) return <div className="card empty muted"><span className="spinner" />Analysing changes against rainfall…</div>
  if (data.detail) return <div className="banner warn">{data.detail}</div>
  if (!data.available) return <div className="banner warn">Not enough clear images yet: {data.reason}</div>
  const m = data.model
  const p = data.detection_power || {}
  const sigma = m.sigma_ha
  const bars = data.intervals.map((i) => ({ ...i, t: Date.parse(i.end) }))
  const years = [...new Set(bars.map((b) => new Date(b.t).getFullYear()))]
  const ticks = years.map((y) => Date.parse(`${y}-01-01`)).filter((t) => bars.length && t >= bars[0].t)
  const inEvent = (i) => data.events.find((e) => e.start <= i.start && i.end <= e.end)

  return (
    <div className="stack">
      <div className="card stack" style={{ gap: '0.5rem' }}>
        <h2 style={{ margin: 0 }}>Changes the rain cannot explain</h2>
        <p className="small" style={{ margin: 0 }}>
          For every pair of consecutive clear images, a model of <em>this lake's</em> usual response to rain predicts how much
          open water should change given the rain that fell. A change far outside that (≥ 3σ) is flagged: something other than
          rain happened — draining, a sluice opened, de-weeding, weeds spreading, or sewage / treated water flowing in.
        </p>
        <div className="kpis">
          <Kpi label="Events found" value={data.events.length} sub={`in ${m.intervals} image intervals`} />
          <Kpi label="Rain response" value={`+${Math.max(0, m.rain_response_ha_per_100mm_empty).toFixed(1)} ha`} sub="per 100 mm when nearly empty" />
          <Kpi label="Normal noise (σ)" value={ha(sigma)} sub={m.suppressed_thin_cirrus ? `${m.suppressed_thin_cirrus} candidate(s) set aside: thin cirrus in an image` : 'weeds drifting, haze, image differences'} />
          <Kpi label="Smallest detectable" value={p.min_detectable_ha ? `${p.min_detectable_ha} ha` : 'n/a'}
               sub={p.min_detectable_pct_of_outline ? `${p.min_detectable_pct_of_outline}% of the outline (80% detection)` : ''} />
        </div>
      </div>

      <div className="card">
        <h3>Unexplained change per image interval</h3>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={bars} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
            <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} ticks={ticks}
                   tickFormatter={(t) => new Date(t).getFullYear()} />
            <YAxis unit=" ha" width={70} />
            <Tooltip labelFormatter={(t) => new Date(t).toISOString().slice(0, 10)}
                     formatter={(v, _n, item) => [`${v > 0 ? '+' : ''}${v} ha (${item.payload.score}σ) · ${item.payload.rain_mm} mm rain`, `${item.payload.start} → ${item.payload.end}`]} />
            <ReferenceLine y={3 * sigma} stroke="#b42318" strokeDasharray="4 4" />
            <ReferenceLine y={-3 * sigma} stroke="#b42318" strokeDasharray="4 4" />
            <ReferenceLine y={0} stroke="#666" />
            <Bar isAnimationActive={false} dataKey="unexplained_ha" barSize={4}>
              {bars.map((b) => {
                const e = inEvent(b)
                return <Cell key={b.start} fill={e ? EVENT_STYLE[e.type].color : '#c5ced8'} />
              })}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <p className="muted small" style={{ marginBottom: 0 }}>
          Grey = within this lake's normal behaviour. Coloured = part of a flagged event. Dashed = ±3σ for a single interval
          (several small same-direction changes in a row can also add up to an event).
        </p>
      </div>

      {data.events.length === 0 && <div className="card empty muted">No change that rain cannot explain was found for this lake.</div>}
      {data.events.map((e) => <EventCard key={e.start + e.type} id={id} e={e} />)}

      {p.recall_by_size_ha && (
        <div className="card">
          <h3>How big must a change be to be caught?</h3>
          <p className="small" style={{ marginTop: 0 }}>
            Synthetic drains of each size were added to every one of {p.positions_tested} real image intervals of this lake;
            the table shows how often the detector found them.
          </p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Drain size</th>{Object.keys(p.recall_by_size_ha).map((s) => <th key={s} className="num">{s} ha</th>)}</tr></thead>
              <tbody><tr><td>Detected</td>{Object.values(p.recall_by_size_ha).map((r, i) => (
                <td key={i} className="num" style={{ color: r >= 0.8 ? '#15803d' : r < 0.3 ? '#b42318' : undefined }}><strong>{Math.round(r * 100)}%</strong></td>
              ))}</tr></tbody>
            </table>
          </div>
        </div>
      )}
      <p className="muted small" style={{ margin: 0 }}>
        Rainfall: {data.rain_source}. An event means "rain does not explain this", not proof of who caused it — the before/after
        images show what changed. Only cloud-masked 10 m archive image pairs without thin cirrus over the lake can form events.
      </p>
    </div>
  )
}

function EventCard({ id, e }) {
  const style = EVENT_STYLE[e.type]
  const before = useRender(id, e.before.item_id)
  const after = useRender(id, e.after.item_id)
  return (
    <div className="card stack" style={{ borderLeft: `5px solid ${style.color}` }}>
      <div className="row">
        <span className="pill" style={{ background: style.color }}>{style.short}</span>
        <strong>{e.start} → {e.end}</strong>
        <span className="muted small">{e.days} days · {e.strength}σ</span>
        <span className="spacer" />
        <span className="mono"><strong>{e.change_ha > 0 ? '+' : ''}{e.change_ha} ha</strong> observed · rain explains {e.expected_change_ha > 0 ? '+' : ''}{e.expected_change_ha} ha</span>
      </div>
      <p className="small" style={{ margin: 0 }}>{e.explanation}</p>
      {e.also_at?.length > 0 && (
        <div className="banner warn small">
          Same-direction unexplained change at the same time at nearby {e.also_at.map((o) => `${o.name} (${o.change_ha > 0 ? '+' : ''}${o.change_ha} ha)`).join(', ')} —
          consistent with a connected cascade or a coordinated operation.
        </div>
      )}
      <div className="grid grid-2">
        {[['Before', e.before, before], ['After', e.after, after]].map(([label, info, img]) => (
          <figure key={label}>
            {img.error ? <div className="loading-box">{img.error}</div>
              : !img.data ? <div className="loading-box"><span><span className="spinner" />{info.date}</span></div>
              : <img className="sat wide" src={img.data.images.overlay} alt={`${label} ${info.date}`} />}
            <figcaption>{label}: {info.date} · {img.data ? `${ha(img.data.open_water_ha)} open water` : info.source}</figcaption>
          </figure>
        ))}
      </div>
      <div className="row small muted" style={{ gap: '1rem' }}>
        <span>Rain: <strong>{e.rain_mm} mm</strong>{e.rain_normal_mm != null && ` (normal ${e.rain_normal_mm} mm)`}</span>
        <span>Green growth inside outline: {e.green_growth_change_ha > 0 ? '+' : ''}{e.green_growth_change_ha} ha</span>
        <span>Unexplained: {e.unexplained_ha > 0 ? '+' : ''}{e.unexplained_ha} ha</span>
        {e.mixed_sources && <span>mixed image sources</span>}
      </div>
    </div>
  )
}

function Kpi({ label, value, sub }) {
  return <div className="kpi"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="sub">{sub}</div>}</div>
}
