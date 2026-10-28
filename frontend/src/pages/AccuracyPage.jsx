import { useEffect, useState } from 'react'
import { api, frac } from '../api.js'
import { AgreementTable, ConfusionMatrix, PerClassTable } from '../components/AccuracyParts.jsx'
import { kappaWord } from '../components/LakeAccuracy.jsx'

export default function AccuracyPage() {
  const [acc, setAcc] = useState(null)
  const [ev, setEv] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    api.accuracyAll().then(setAcc, (e) => setError(e.message))
    api.cityEvents().then(setEv, () => {})
  }, [])
  if (error) return <div className="banner danger">{error}</div>
  if (!acc) return <p className="muted"><span className="spinner" />Loading…</p>
  const m = acc.model
  const meas = acc.measurement
  const val = m.validation_scene_14oct26
  const firstAcc = m.first_model_rule_labels.filter((r) => r.source === 'model')

  return (
    <div className="stack">
      <div>
        <h1>How accurate is AquaRevive?</h1>
        <p className="muted" style={{ margin: '0.3rem 0 0' }}>Every number here comes from the project's own evaluation runs on real Sentinel-2 data.</p>
      </div>

      <div className="card stack">
        <h2 style={{ margin: 0 }}>1. Lake open-water measurement <span className="muted small">(the main result)</span></h2>
        <p className="small" style={{ margin: 0 }}>
          {meas.comparisons} lake-dates across {meas.lakes} lakes, compared with three independent products. *Agree = {meas.agreement_rule}.
        </p>
        <AgreementTable byRef={meas.by_reference} references={meas.references} />
        <p className="muted small" style={{ margin: 0 }}>{meas.note}</p>
      </div>

      <div className="card stack">
        <h2 style={{ margin: 0 }}>2. Land-cover U-Net vs ESA WorldCover</h2>
        <p className="small" style={{ margin: 0 }}>{m.test_design}</p>
        <div className="kpis">
          {m.heldout.map((h) => (
            <div className="kpi" key={h.scene}><div className="label">Held-out {h.date}</div>
              <div className="value">{frac(h.accuracy)}</div><div className="sub">mean IoU {frac(h.mean_iou)}</div></div>
          ))}
          {val && <div className="kpi"><div className="label">Unseen scene 2026-10-14</div><div className="value">{frac(val.accuracy)}</div>
            <div className="sub">kappa {val.kappa} ({kappaWord(val.kappa)})</div></div>}
          {firstAcc.length > 0 && <div className="kpi"><div className="label">First model (rule labels)</div>
            <div className="value" style={{ color: '#b42318' }}>{frac(Math.max(...firstAcc.map((f) => f.accuracy)))}</div>
            <div className="sub">why it was retrained on WorldCover</div></div>}
        </div>
        <div className="grid grid-2">
          {m.heldout.map((h) => (
            <div key={h.scene}><h3>Held-out part of {h.date}</h3><PerClassTable perClass={h.per_class} /></div>
          ))}
        </div>
        {val && (
          <div className="grid grid-2">
            <div><h3>Confusion matrix, unseen 2026-10-14 scene</h3><ConfusionMatrix matrix={val.confusion_matrix} /></div>
            <div><h3>Per class, 2026-10-14</h3><PerClassTable perClass={val.per_class} /></div>
          </div>
        )}
        {m.v1_vs_v2?.length > 0 && (
          <div className="stack" style={{ gap: '0.4rem' }}>
            <h3 style={{ margin: 0 }}>Model v2 (in use) vs v1 — same held-out pixels</h3>
            <p className="small" style={{ margin: 0 }}>
              v2 was trained on 10 extra Sentinel-2 images from all seasons of 2020–2021 (10 m) besides the two April scenes.
              "New dates" were never seen in training, and are scored only on the never-trained right 30% of the area.
            </p>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Model</th><th>Test</th><th className="num">Accuracy</th><th className="num">Kappa</th>
                  <th className="num">Mean IoU</th><th className="num">Water P / R</th><th className="num">Bare land P / R</th></tr></thead>
                <tbody>
                  {m.v1_vs_v2.map((r) => (
                    <tr key={r.model + r.test} style={{ fontWeight: r.model.startsWith('v2') ? 600 : undefined }}>
                      <td>{r.model}</td><td className="small">{r.test}</td>
                      <td className="num">{frac(r.accuracy)}</td><td className="num">{r.kappa?.toFixed(2)}</td>
                      <td className="num">{frac(r.mean_iou)}</td>
                      <td className="num">{frac(r.water_precision, 0)} / {frac(r.water_recall, 0)}</td>
                      <td className="num">{frac(r.bare_land_precision, 0)} / {frac(r.bare_land_recall, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small" style={{ margin: 0 }}>
              v2 finds bare land (dry lakebeds, construction, cleared land) far more often (recall 6% → 41%) and scores higher on kappa
              and mean IoU, which reward all four classes fairly; plain accuracy is about the same because vegetation and built-up
              dominate. Trade-off: more false bare land (precision 46% → 26%). The figures above this table describe v1.
            </p>
          </div>
        )}
        <div className="banner warn small">{m.caveat} Water is over-predicted (low precision) and bare land is the weakest class, so city-wide land-cover change should not be quoted as a precise number.</div>
      </div>

      {ev && (
        <div className="card stack">
          <h2 style={{ margin: 0 }}>3. Change-event detector <span className="muted small">(the novelty)</span></h2>
          <h3 style={{ margin: 0 }}>Documented events</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Lake</th><th>Window</th><th>What happened (source)</th><th>Expected</th><th>Detected</th></tr></thead>
              <tbody>
                {ev.validation.cases.map((c) => {
                  const lake = ev.lakes.find((l) => l.lake_id === c.lake_id)
                  return (
                    <tr key={c.lake_id + c.start}>
                      <td>{c.lake}</td>
                      <td className="small">{c.start} → {c.end}</td>
                      <td className="small"><a href={c.source} target="_blank" rel="noreferrer">{c.what_happened}</a></td>
                      <td>{c.expected}</td>
                      <td>{c.detected ? <strong style={{ color: '#15803d' }}>Yes</strong>
                        : <span style={{ color: '#b42318' }}>No</span>}
                        {!c.detected && lake && <div className="muted small">min detectable here: {lake.min_detectable_ha} ha</div>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <h3 style={{ margin: 0 }}>Smallest change each lake can reveal (synthetic-drain test)</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Lake</th><th className="num">Outline</th><th className="num">Noise σ</th><th className="num">Detectable (80%)</th><th className="num">Events</th></tr></thead>
              <tbody>
                {ev.lakes.map((l) => (
                  <tr key={l.lake_id}>
                    <td>{l.name}</td><td className="num">{l.outline_ha} ha</td><td className="num">{l.sigma_ha} ha</td>
                    <td className="num">{l.min_detectable_ha ? `${l.min_detectable_ha} ha (${l.min_detectable_pct_of_outline}%)` : '—'}</td>
                    <td className="num">{l.events}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            {ev.validation.note} Large weed-covered lakes (Bellandur) have high natural noise from drifting weed mats, so only very large
            changes are detectable there; adding more documented events to ai_agent/validation_events.csv improves this evaluation.
          </p>
        </div>
      )}
    </div>
  )
}
