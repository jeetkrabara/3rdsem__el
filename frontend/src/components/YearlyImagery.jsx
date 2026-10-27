import { useState } from 'react'
import { ha } from '../api.js'
import { useRender } from '../renderQueue.js'

const VIEWS = { overlay: 'Measured water', truecolor: 'Natural colour', mndwi: 'Water index' }

export default function YearlyImagery({ id, imagery, changeYear }) {
  const years = imagery?.years || []
  const usable = years.filter((y) => !y.missing)
  const [view, setView] = useState('overlay')
  const [left, setLeft] = useState(usable[0]?.year)
  const [right, setRight] = useState(usable.at(-1)?.year)

  if (!usable.length) return <div className="card empty muted">No clear satellite image of this lake has been measured yet.</div>

  return (
    <div className="stack">
      <div className="card stack">
        <div className="row">
          <div>
            <h2 style={{ margin: 0 }}>One real image per year</h2>
            <p className="muted small" style={{ margin: '0.25rem 0 0' }}>
              The clearest pre-monsoon (Feb–May) Sentinel-2 image of each year, so years are comparable.
              Each picture is re-measured live; first loads download from the archive and take ~20 s.
              {changeYear && <> Change point: <strong>◆ {changeYear}</strong>.</>}
            </p>
          </div>
          <span className="spacer" />
          <Segmented value={view} onChange={setView} options={VIEWS} />
        </div>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))' }}>
          {years.map((y) => <YearTile key={y.year} id={id} y={y} view={view} changeYear={changeYear} />)}
        </div>
        <Legend view={view} />
      </div>

      <div className="card stack">
        <div className="row">
          <h2 style={{ margin: 0 }}>Compare two years</h2>
          <span className="spacer" />
          <YearSelect label="Earlier" years={usable} value={left} onChange={setLeft} />
          <YearSelect label="Later" years={usable} value={right} onChange={setRight} />
        </div>
        <div className="grid grid-2">
          {[left, right].map((yr, i) => {
            const y = usable.find((u) => u.year === yr)
            return y ? <YearDetail key={`${i}-${yr}`} id={id} y={y} view={view} /> : null
          })}
        </div>
      </div>
    </div>
  )
}

function YearTile({ id, y, view, changeYear }) {
  const { data, error } = useRender(id, y.missing ? null : y.item_id)
  return (
    <figure>
      {y.missing ? <div className="loading-box">{y.year}: all {y.cloudy_dates} images cloudy</div>
        : error ? <div className="loading-box" style={{ color: '#8a1010' }}>{error}</div>
        : !data ? <div className="loading-box"><span><span className="spinner" />{y.date}</span></div>
        : <img className="sat wide" src={data.images[view]} alt={`${y.year} ${view}`} loading="lazy" />}
      <figcaption>
        <strong>{y.year}</strong>{y.year === changeYear && ' ◆'}
        {!y.missing && <> · {ha(y.open_water_ha)} open water<br />{y.date} · {y.source === 'archive' ? '10 m archive' : `${y.pixel_m} m local scene`}{!y.pre_monsoon && ' · not pre-monsoon'}</>}
      </figcaption>
    </figure>
  )
}

function YearDetail({ id, y, view }) {
  const dates = [y, ...(y.other_dates || [])].sort((a, b) => a.date.localeCompare(b.date))
  const [itemId, setItemId] = useState(y.item_id)
  const pick = dates.find((d) => d.item_id === itemId) || y
  const { data, error } = useRender(id, itemId)
  return (
    <div className="stack" style={{ gap: '0.5rem' }}>
      <label className="field">{y.year} — date
        <select value={itemId} onChange={(e) => setItemId(e.target.value)}>
          {dates.map((d) => <option key={d.item_id} value={d.item_id}>{d.date} · {ha(d.open_water_ha)} · {d.source}</option>)}
        </select>
      </label>
      {error ? <div className="banner danger">{error}</div>
        : !data ? <div className="loading-box"><span><span className="spinner" />Fetching {pick.date}…</span></div>
        : <img className="sat wide" src={data.images[view]} alt={`${pick.date}`} />}
      {data && (
        <div className="small">
          <strong>{ha(data.open_water_ha)}</strong> open water of {ha(data.outline_ha, 0)} outline
          ({data.water_pct_of_outline}%){data.vegetation_ha != null && <> · {ha(data.vegetation_ha)} green growth</>}
          <div className="muted">{data.satellite} · {data.pixel_m} m · {Math.round(data.valid_frac * 100)}% of the outline cloud-free · threshold {data.threshold} ({data.threshold_method})</div>
        </div>
      )}
    </div>
  )
}

function YearSelect({ label, years, value, onChange }) {
  return (
    <label className="field">{label}
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {years.map((y) => <option key={y.year} value={y.year}>{y.year}</option>)}
      </select>
    </label>
  )
}

export function Segmented({ value, onChange, options }) {
  return (
    <div className="segmented">
      {Object.entries(options).map(([k, v]) => (
        <button key={k} className={value === k ? 'active' : ''} onClick={() => onChange(k)}>{v}</button>
      ))}
    </div>
  )
}

function Legend({ view }) {
  if (view === 'overlay') return (
    <div className="legend">
      <span><span className="swatch" style={{ background: '#ffe13c' }} />lake outline (OpenStreetMap)</span>
      <span><span className="swatch" style={{ background: '#00ebff' }} />edge of measured open water</span>
    </div>
  )
  if (view === 'mndwi') return (
    <div>
      <div className="gradient" style={{ background: 'linear-gradient(90deg,#a50026,#fdfebf,#313695)' }} />
      <div className="row small muted"><span>dry / plants</span><span className="spacer" /><span>that date's threshold</span><span className="spacer" /><span>counted as water</span></div>
    </div>
  )
  return <p className="muted small" style={{ margin: 0 }}>Natural colour (red, green, blue bands) with the same brightness scale every year. Grey = cloud.</p>
}
