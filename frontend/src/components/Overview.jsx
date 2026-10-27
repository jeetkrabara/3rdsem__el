import { CLASS_COLORS, CLASS_LABELS, frac, ha, pct } from '../api.js'
import { useRender } from '../renderQueue.js'
import LakesMap from './LakesMap.jsx'

export default function Overview({ id, a, imagery, geo }) {
  const an = a.anomaly || {}
  const cp = a.change_point || {}
  const pr = a.priority
  const lc = a.segmentation?.land_cover
  // show the image the headline number was measured on, else the newest clear one
  const usable = (imagery?.years || []).filter((y) => !y.missing)
  const latestPick = usable.flatMap((y) => [y, ...(y.other_dates || [])]).find((d) => d.date === an.date && d.source === an.source) || usable.at(-1)
  const img = useRender(id, latestPick?.item_id)
  const deficit = an.deficit_fraction

  return (
    <div className="stack">
      <div className="kpis">
        <Kpi label="Open water" value={ha(an.observed_water_ha)} sub={an.date ? `on ${an.date}` : an.note} />
        <Kpi label="Normal for this season" value={ha(an.expected_water_ha)} sub={an.years_in_normal ? `median of ${an.years_in_normal} other years` : ''} />
        <Kpi label="Shortfall" value={deficit == null ? '—' : frac(deficit, 0)}
             sub={deficit == null ? '' : deficit > 0 ? 'below normal' : 'above normal'}
             color={deficit > 0.15 ? '#b42318' : undefined} />
        <Kpi label="Outline area" value={ha(a.segmentation?.lake?.outline_ha ?? null, 0)} sub="OpenStreetMap" />
        <Kpi label="Change point" value={cp.change_point_year ?? 'None'} sub="pre-monsoon level shift" />
        <Kpi label="Priority" value={pr ? `#${pr.rank}` : '—'} sub={pr ? `score ${pr.priority_score} of ${a.lakes_ranked} lakes` : 'not ranked'} />
      </div>

      <div className="card stack" style={{ gap: '0.5rem' }}>
        <h3 style={{ margin: 0 }}>What the satellite shows</h3>
        {cp.narrative && <p style={{ margin: 0 }}>{cp.narrative}</p>}
        {an.interpretation && <p style={{ margin: 0 }}><strong>Plants over water:</strong> {an.interpretation.split(': ').slice(1).join(': ')}</p>}
        {an.note && <p className="muted small" style={{ margin: 0 }}>{an.note}</p>}
      </div>

      <div className="grid grid-3">
        <figure className="card">
          <h3>Image behind the latest number</h3>
          {!latestPick ? <div className="loading-box">No clear image of this lake yet.</div>
            : img.error ? <div className="banner danger">{img.error}</div>
            : !img.data ? <div className="loading-box"><span><span className="spinner" />Fetching {latestPick.date} from Sentinel-2…</span></div>
            : <img className="sat wide" src={img.data.images.overlay} alt="Latest satellite image with measured water" />}
          {img.data && (
            <figcaption>
              {img.data.date} · {img.data.satellite} · {img.data.pixel_m} m pixels.
              Yellow = lake outline, cyan = measured open water ({ha(img.data.open_water_ha)}).
            </figcaption>
          )}
        </figure>

        <div className="card">
          <h3>Land cover around the lake</h3>
          {lc ? (
            <>
              {Object.entries(lc.class_percentages).map(([k, v]) => (
                <div key={k} className="small" style={{ marginBottom: 8 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="swatch" style={{ background: CLASS_COLORS[k] }} />{CLASS_LABELS[k]}
                    <span className="spacer" /><span className="mono">{pct(v)}</span>
                  </div>
                  <div className="bar"><span style={{ width: `${v}%`, background: CLASS_COLORS[k] }} /></div>
                </div>
              ))}
              <p className="muted small" style={{ marginBottom: 0 }}>
                From the U-Net land-cover model on the {lc.scene_date} scene ({lc.pixels.toLocaleString()} pixels in the box
                around the lake). About 86% accurate against WorldCover; weakest for water precision and bare land.
              </p>
            </>
          ) : <p className="muted small">No land-cover prediction near this date.</p>}
        </div>

        <div className="card">
          <h3>Location</h3>
          <LakesMap data={geo} focusId={id} height={300} />
        </div>
      </div>
    </div>
  )
}

function Kpi({ label, value, sub, color }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value" style={{ color }}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}
