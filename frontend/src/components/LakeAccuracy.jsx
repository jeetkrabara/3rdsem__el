import { Link } from 'react-router-dom'
import { frac } from '../api.js'
import { AgreementTable, ConfusionMatrix, CrossCheckChart, PerClassTable } from './AccuracyParts.jsx'

export default function LakeAccuracy({ acc }) {
  if (!acc) return <div className="card empty muted"><span className="spinner" />Loading accuracy…</div>
  const meas = acc.measurement
  const lakeModel = acc.lake_model_vs_worldcover || []

  return (
    <div className="stack">
      <div className="card stack">
        <h2 style={{ margin: 0 }}>Is the open-water number right? <span className="muted small">(this lake)</span></h2>
        <p className="small" style={{ margin: 0 }}>
          The same lake on the same dates, measured independently by other satellite products. None is ground truth,
          but agreement with several independent sources is strong evidence. *Agree = {meas.agreement_rule}.
        </p>
        {meas.comparisons ? (
          <>
            <AgreementTable byRef={meas.by_reference} references={meas.references} title="This lake vs" />
            <CrossCheckChart points={meas.points} references={meas.references} />
          </>
        ) : <div className="banner warn">This lake was not in the cross-check sample. City-wide results are below.</div>}
        <h3 style={{ marginBottom: 0 }}>All lakes in the cross-check</h3>
        <AgreementTable byRef={acc.measurement_overall} references={meas.references} title="All lakes vs" />
        <p className="muted small" style={{ margin: 0 }}>{meas.note}</p>
      </div>

      <div className="card stack">
        <h2 style={{ margin: 0 }}>Is the land-cover model right here? <span className="muted small">(vs ESA WorldCover)</span></h2>
        {lakeModel.length === 0 ? (
          <div className="banner warn">The two WorldCover-labelled scenes (2020, 2021) do not cover this lake.</div>
        ) : (
          <div className="grid grid-2">
            {lakeModel.map((m) => (
              <div key={m.scene} className="stack" style={{ gap: '0.5rem' }}>
                <h3 style={{ margin: 0 }}>{m.date}</h3>
                <div className="kpis">
                  <div className="kpi"><div className="label">Accuracy</div><div className="value">{frac(m.accuracy)}</div></div>
                  <div className="kpi"><div className="label">Kappa</div><div className="value">{m.kappa == null ? "—" : Math.abs(m.kappa).toFixed(2) === "0.00" ? "0.00" : m.kappa.toFixed(2)}</div>
                    <div className="sub">{kappaWord(m.kappa)}</div></div>
                  <div className="kpi"><div className="label">Pixels</div><div className="value">{m.pixels.toLocaleString()}</div></div>
                </div>
                <ConfusionMatrix matrix={m.confusion_matrix} />
                <PerClassTable perClass={m.per_class} />
              </div>
            ))}
          </div>
        )}
        {lakeModel.some((m) => m.accuracy > 0.85 && (m.kappa ?? 1) < 0.2) && (
          <div className="banner warn small">
            High accuracy but kappa near 0: almost the whole outline is one class in both maps (usually vegetation — weeds or
            marsh), so the model scores well just by predicting that class. Treat this lake's model accuracy as uninformative.
          </div>
        )}
        <p className="muted small" style={{ margin: 0 }}>
          WorldCover is itself a model (~75% accurate). See the <Link to="/accuracy">full accuracy report</Link> for the held-out test.
        </p>
      </div>
    </div>
  )
}

export function kappaWord(k) {
  if (k == null) return ''
  if (k < 0.2) return 'no better than chance'
  if (k < 0.4) return 'fair agreement'
  if (k < 0.6) return 'moderate agreement'
  if (k < 0.8) return 'substantial agreement'
  return 'almost perfect agreement'
}
