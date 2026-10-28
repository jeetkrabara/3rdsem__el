import { useEffect, useState } from 'react'
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, CLASS_COLORS, CLASS_LABELS, ha } from '../api.js'

const ZONE_TEXT = {
  inside: 'Inside the lake outline (lakebed)',
  '0-30 m': '0–30 m from the shore — protected buffer (KTCDA)',
  '30-75 m': '30–75 m — wider buffer (2016 NGT order)',
  '75-200 m': '75–200 m — the neighbourhood',
}
const CLASSES = ['water', 'vegetation', 'bare_land', 'built_up']

export default function LandAround({ id }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [zone, setZone] = useState('30-75 m')
  const [year, setYear] = useState(null)
  useEffect(() => {
    setData(null); setError(null)
    api.landcover(id).then((d) => { setData(d); setYear(d.years.at(-1)?.year) }, (e) => setError(e.message))
  }, [id])

  if (error) return <div className="banner danger">{error}</div>
  if (!data) return <div className="card empty muted"><span className="spinner" />Running the land-cover model on one image per year — the first time takes 2–3 minutes for this lake…</div>
  if (!data.years.length) return <div className="card empty muted">No clear images to analyse yet.</div>

  const rows = data.years.map((y) => ({ year: y.year, ...Object.fromEntries(CLASSES.map((c) => [c, y.rings[zone][c]])) }))
  const shown = data.years.find((y) => y.year === year) || data.years.at(-1)
  const ch = data.change

  return (
    <div className="stack">
      <div className="card stack" style={{ gap: '0.5rem' }}>
        <h2 style={{ margin: 0 }}>What is happening to the land around the lake?</h2>
        <p className="small" style={{ margin: 0 }}>
          The land-cover AI model labels every pixel as water, vegetation, bare land or built-up on one clear image per year.
          Zones follow the shoreline, so built-up land creeping into the buffers is visible as encroachment.
        </p>
      </div>

      {ch && (
        <div className="card">
          <h3>Change: {ch.from_years[0]}–{ch.from_years.at(-1)} vs {ch.to_years[0]}–{ch.to_years.at(-1)} (3-year averages)</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Zone</th>{CLASSES.map((c) => <th key={c} className="num">{CLASS_LABELS[c]}</th>)}</tr></thead>
              <tbody>
                {data.zones.map((z) => (
                  <tr key={z}>
                    <td className="small">{ZONE_TEXT[z]}</td>
                    {CLASSES.map((c) => {
                      const v = ch.zones[z][c]
                      const strong = v && Math.abs(v.change_ha) >= 0.5
                      const bad = v && ((c === 'built_up' && v.change_ha > 0 && z !== 'inside') || (c === 'water' && z === 'inside' && v.change_ha < 0))
                      return (
                        <td key={c} className="num" style={{ color: strong ? (bad ? '#b42318' : '#15803d') : undefined }}>
                          {v ? `${v.change_ha > 0 ? '+' : ''}${v.change_ha.toFixed(1)} ha` : '—'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small" style={{ marginBottom: 0 }}>Red = built-up growing in a buffer or water lost from the lakebed.</p>
        </div>
      )}

      <div className="card">
        <div className="row">
          <h3 style={{ margin: 0 }}>Over the years</h3>
          <span className="spacer" />
          <label className="field">Zone
            <select value={zone} onChange={(e) => setZone(e.target.value)}>
              {data.zones.map((z) => <option key={z} value={z}>{ZONE_TEXT[z]}</option>)}
            </select>
          </label>
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <AreaChart data={rows} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
            <XAxis dataKey="year" />
            <YAxis unit=" ha" width={70} />
            <Tooltip formatter={(v, n) => [ha(v), n]} />
            <Legend />
            {CLASSES.map((c) => (
              <Area key={c} isAnimationActive={false} type="monotone" dataKey={c} name={CLASS_LABELS[c]} stackId="1"
                    stroke={CLASS_COLORS[c]} fill={CLASS_COLORS[c]} fillOpacity={0.75} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="card">
        <div className="row">
          <h3 style={{ margin: 0 }}>Land-cover map</h3>
          <span className="spacer" />
          <label className="field">Year
            <select value={shown.year} onChange={(e) => setYear(Number(e.target.value))}>
              {data.years.map((y) => <option key={y.year} value={y.year}>{y.year} ({y.date})</option>)}
            </select>
          </label>
        </div>
        <img className="sat wide" src={shown.map} alt={`Land cover ${shown.year}`} style={{ maxWidth: 720 }} />
        <div className="legend">
          {CLASSES.map((c) => <span key={c}><span className="swatch" style={{ background: CLASS_COLORS[c] }} />{CLASS_LABELS[c]}</span>)}
          <span><span className="swatch" style={{ background: '#fff', border: '1px solid #ccc' }} />zone edges (outline, 30 m, 75 m, 200 m)</span>
          <span><span className="swatch" style={{ background: '#808080' }} />cloud</span>
        </div>
        <p className="muted small" style={{ marginBottom: 0 }}>
          {shown.date} · {shown.source === 'archive' ? '10 m archive image' : `${shown.pixel_m.toFixed(0)} m downloaded scene`} · model {data.model}. {data.note}
        </p>
      </div>
    </div>
  )
}
