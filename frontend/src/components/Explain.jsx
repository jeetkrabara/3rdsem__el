import { useEffect, useState } from 'react'
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, b64, CLASS_COLORS, CLASS_LABELS, ha, pct } from '../api.js'
import { useRender } from '../renderQueue.js'

export default function Explain({ id, imagery }) {
  return (
    <div className="stack">
      <MeasurementWhy id={id} imagery={imagery} />
      <ModelWhy id={id} />
    </div>
  )
}

// ------------------------------------------------ why this many hectares?
function MeasurementWhy({ id, imagery }) {
  const dates = (imagery?.years || []).filter((y) => !y.missing)
    .flatMap((y) => [y, ...(y.other_dates || [])]).sort((a, b) => b.date.localeCompare(a.date))
  const [itemId, setItemId] = useState(imagery?.years?.filter((y) => !y.missing).at(-1)?.item_id)
  const { data, error } = useRender(id, itemId)

  if (!dates.length) return null
  return (
    <div className="card stack">
      <div className="row">
        <div>
          <h2 style={{ margin: 0 }}>Why this much open water? <span className="muted small">(the lake measurement)</span></h2>
          <p className="muted small" style={{ margin: '0.25rem 0 0' }}>
            The main result is a transparent rule, so it can be shown exactly: every pixel's water index, the
            threshold chosen for that image, and which pixels passed.
          </p>
        </div>
        <span className="spacer" />
        <label className="field">Image
          <select value={itemId} onChange={(e) => setItemId(e.target.value)}>
            {dates.map((d) => <option key={d.item_id} value={d.item_id}>{d.date} · {ha(d.open_water_ha)}</option>)}
          </select>
        </label>
      </div>
      {error && <div className="banner danger">{error}</div>}
      {!data && !error && <div className="loading-box" style={{ aspectRatio: 'auto', padding: '2rem' }}><span><span className="spinner" />Fetching the image and re-measuring…</span></div>}
      {data && (
        <>
          <div className="banner ok small">
            <strong>Rule:</strong> {data.rule}. Result: <strong>{ha(data.open_water_ha, 2)}</strong>
            {data.matches_stored ? ' — identical to the stored measurement.' : ` (stored value ${ha(data.stored_open_water_ha, 2)}; the code has changed since).`}
          </div>
          <div className="grid grid-3">
            <figure>
              <img className="sat wide" src={data.images.truecolor} alt="Natural colour" />
              <figcaption>Natural colour, {data.date} ({data.satellite}).</figcaption>
            </figure>
            <figure>
              <img className="sat wide" src={data.images.mndwi} alt="MNDWI" />
              <div className="gradient" style={{ background: 'linear-gradient(90deg,#a50026,#fdfebf,#313695)' }} />
              <figcaption>Water index (MNDWI). Colour scale centred on this image's threshold {data.threshold}: blue passes.</figcaption>
            </figure>
            <figure>
              <img className="sat wide" src={data.images.overlay} alt="Measured water" />
              <figcaption>Result: cyan = edge of the {ha(data.open_water_ha)} counted, yellow = outline. Water outside the outline is not counted.</figcaption>
            </figure>
          </div>
          <div>
            <h3>How the threshold was chosen</h3>
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={data.histogram} margin={{ top: 16, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
                <XAxis dataKey="mndwi" type="number" domain={[-1, 1]} ticks={[-1, -0.5, 0, 0.5, 1]} />
                <YAxis width={60} />
                <Tooltip labelFormatter={(v) => `MNDWI ≈ ${v}`} />
                <Legend />
                <Area isAnimationActive={false} type="stepAfter" dataKey="around" name="Pixels around the lake (150 m ring)" stackId="a" stroke="#a88a44" fill="#c8aa64" fillOpacity={0.8} />
                <Area isAnimationActive={false} type="stepAfter" dataKey="inside" name="Pixels inside the outline" stackId="a" stroke={CLASS_COLORS.water} fill={CLASS_COLORS.water} fillOpacity={0.8} />
                <ReferenceLine x={data.threshold} stroke="#b42318" strokeWidth={2} label={{ value: `threshold ${data.threshold}`, position: 'top', fontSize: 11 }} />
              </AreaChart>
            </ResponsiveContainer>
            <p className="muted small" style={{ marginBottom: 0 }}>
              {data.threshold_method.startsWith('otsu')
                ? 'Otsu: the threshold sits in the valley between the "water" and "land" humps of this histogram, so it adapts to each date’s light and haze.'
                : 'The histogram was not clearly two-humped, so the safe default threshold was used instead of an adaptive one.'}
              {' '}Pixels must also be dark in near-infrared (&lt; 0.20), which rejects wet soil and shadows.
            </p>
          </div>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------ what does the U-Net rely on?
const METHOD_NOTE = 'These explain the land-cover MODEL, not the ground truth. Inputs are correlated (NDWI is built from green and NIR), so credit can be shared between them.'

function ModelWhy({ id }) {
  const [opts, setOpts] = useState({ scene: '', cls: 'water', shap: true })
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function run(o = opts) {
    setBusy(true); setError(null)
    try {
      const methods = ['occlusion', 'gradcam', ...(o.shap ? ['shap'] : [])]
      setRes(await api.xai(id, { scene: o.scene || undefined, class: o.cls, methods }))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  useEffect(() => { run() }, [id])   // eslint-disable-line react-hooks/exhaustive-deps

  const scenes = res?.available_scenes || []
  const unavailable = res?.model_explanation?.unavailable
  return (
    <div className="card stack">
      <div className="row">
        <div>
          <h2 style={{ margin: 0 }}>What does the AI model look at? <span className="muted small">(U-Net land cover)</span></h2>
          <p className="muted small" style={{ margin: '0.25rem 0 0' }}>Occlusion, Grad-CAM and SHAP on the same real scene, all targeting the lake outline.</p>
        </div>
        <span className="spacer" />
        <label className="field">Scene
          <select value={opts.scene || res?.scene || ''} onChange={(e) => setOpts({ ...opts, scene: e.target.value })}>
            {scenes.map((s) => <option key={s.scene} value={s.scene}>{s.date}</option>)}
          </select>
        </label>
        <label className="field">Explain class
          <select value={opts.cls} onChange={(e) => setOpts({ ...opts, cls: e.target.value })}>
            {Object.entries(CLASS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">SHAP
          <select value={opts.shap ? 'y' : 'n'} onChange={(e) => setOpts({ ...opts, shap: e.target.value === 'y' })}>
            <option value="y">on (~10 s)</option><option value="n">off</option>
          </select>
        </label>
        <button className="btn" onClick={() => run()} disabled={busy}>{busy ? <><span className="spinner" />Explaining…</> : 'Explain'}</button>
      </div>
      {error && <div className="banner danger">{error}</div>}
      {unavailable && <div className="banner warn">{unavailable}</div>}
      {busy && !res && <div className="loading-box" style={{ aspectRatio: 'auto', padding: '2rem' }}><span><span className="spinner" />Running the model on the scene…</span></div>}
      {res?.prediction && <ModelResults res={res} />}
      <p className="muted small" style={{ margin: 0 }}>{METHOD_NOTE}</p>
    </div>
  )
}

function ModelResults({ res }) {
  const pred = res.prediction
  const occ = res.model_explanation
  const cam = res.gradcam
  const shap = res.shap
  const occData = (occ?.inputs || []).map((r) => ({ name: short(r.input), value: r.pixels_changed_pct, full: r.input }))
  const shapData = (shap?.inputs || []).map((r) => ({ name: short(r.input), value: r.contribution_pts, full: r.input }))

  return (
    <div className="stack">
      <div className="grid grid-3">
        <figure>
          <h3>Scene {pred.scene_date} (false colour)</h3>
          <img className="sat wide" src={b64(pred.false_colour_png_base64)} alt="False colour" />
          <figcaption>Near-infrared, red, green: plants red, water dark. Yellow = lake outline. {pred.pixel_m} m pixels.</figcaption>
        </figure>
        <figure>
          <h3>Model prediction</h3>
          <img className="sat wide" src={b64(pred.prediction_png_base64)} alt="Prediction" />
          <div className="legend">
            {Object.entries(pred.class_colors).map(([k, c]) => <span key={k}><span className="swatch" style={{ background: c }} />{CLASS_LABELS[k]} {pct(pred.predicted_shares_pct[k])}</span>)}
          </div>
          <figcaption>Shares are inside the lake outline ({pred.pixels_in_area.toLocaleString()} pixels).</figcaption>
        </figure>
        <figure>
          <h3>Grad-CAM: where the {res.class.replace('_', ' ')} evidence is</h3>
          {cam?.png_base64 ? <img className="sat wide" src={b64(cam.png_base64)} alt="Grad-CAM" /> : <div className="loading-box">{cam?.unavailable}</div>}
          {cam?.png_base64 && <div className="gradient" style={{ background: 'linear-gradient(90deg,#000004,#b73779,#fcfdbf)' }} />}
          <figcaption>{cam?.explanation}</figcaption>
        </figure>
      </div>

      <div className="grid grid-2">
        <div>
          <h3>Occlusion: remove one input, how many lake pixels change class?</h3>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={occData} layout="vertical" margin={{ left: 10, right: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" horizontal={false} />
              <XAxis type="number" unit="%" />
              <YAxis type="category" dataKey="name" width={80} />
              <Tooltip formatter={(v, _, p) => [`${v}% of lake pixels change`, p.payload.full]} />
              <Bar isAnimationActive={false} dataKey="value" fill="#0b6bcb" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <p className="small" style={{ marginBottom: 0 }}>{occ?.explanation}</p>
        </div>

        <div>
          <h3>SHAP: what pushes the lake's {res.class.replace('_', ' ')} probability up or down?</h3>
          {shap?.inputs ? (
            <>
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={shapData} layout="vertical" margin={{ left: 10, right: 30 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" horizontal={false} />
                  <XAxis type="number" unit=" pts" />
                  <YAxis type="category" dataKey="name" width={80} />
                  <ReferenceLine x={0} stroke="#666" />
                  <Tooltip formatter={(v, _, p) => [`${v > 0 ? '+' : ''}${v} probability points`, p.payload.full]} />
                  <Bar isAnimationActive={false} dataKey="value" radius={4}>
                    {shapData.map((d) => <Cell key={d.name} fill={d.value >= 0 ? '#2166ac' : '#b2182b'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <p className="small" style={{ marginBottom: 0 }}>{shap.explanation}</p>
            </>
          ) : <p className="muted small">{shap?.unavailable || 'SHAP is off. Switch it on and press Explain.'}</p>}
        </div>
      </div>

      {shap?.map_png_base64 && (
        <div className="grid grid-3">
          <figure>
            <h3>SHAP map</h3>
            <img className="sat wide" src={b64(shap.map_png_base64)} alt="SHAP map" />
            <div className="gradient" style={{ background: 'linear-gradient(90deg,#b2182b,#f7f7f7,#2166ac)' }} />
            <div className="row small muted"><span>lowers</span><span className="spacer" /><span>raises</span></div>
            <figcaption>Each pixel's total contribution (all inputs) to the lake's {res.class.replace('_', ' ')} probability. Covers {shap.lake_pixels_covered_pct}% of the lake.</figcaption>
          </figure>
          <div className="stack" style={{ gridColumn: 'span 2' }}>
            <div className="kpis">
              <div className="kpi"><div className="label">Model probability (lake)</div><div className="value">{pct(shap.predicted_probability_in_area * 100, 0)}</div></div>
              <div className="kpi"><div className="label">Typical patch</div><div className="value">{pct(shap.background_probability * 100, 0)}</div></div>
              <div className="kpi"><div className="label">Sum of contributions</div><div className="value">{(shap.sum_of_contributions * 100).toFixed(1)} pts</div>
                <div className="sub">should ≈ the difference (gap {(shap.completeness_gap * 100).toFixed(1)} pts)</div></div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function short(name) {
  return name.replace(' (greenness)', '').replace(' (water index)', '').replace(' (built-up index)', '')
    .replace('shortwave-infrared', 'SWIR').replace('near-infrared', 'NIR')
}
