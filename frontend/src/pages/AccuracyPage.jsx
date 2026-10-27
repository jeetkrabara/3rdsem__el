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
