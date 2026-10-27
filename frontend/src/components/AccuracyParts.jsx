import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CLASS_COLORS, CLASS_LABELS, frac, ha, REF_COLORS } from '../api.js'

const CLASSES = ['water', 'vegetation', 'bare_land', 'built_up']

export function ConfusionMatrix({ matrix }) {
  const max = Math.max(...matrix.flat())
  return (
    <div className="table-wrap">
      <table className="cm">
        <thead><tr><th>WorldCover ↓ / model →</th>{CLASSES.map((c) => <th key={c}>{CLASS_LABELS[c]}</th>)}</tr></thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th>{CLASS_LABELS[CLASSES[i]]}</th>
              {row.map((v, j) => {
                const a = max ? Math.sqrt(v / max) : 0
                return (
                  <td key={j} style={{
                    background: i === j ? `rgba(21,128,61,${0.1 + a * 0.6})` : v ? `rgba(180,35,24,${0.06 + a * 0.6})` : undefined,
                    color: a > 0.65 ? '#fff' : undefined,
                  }}>{v.toLocaleString()}</td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PerClassTable({ perClass }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Class</th><th className="num">Precision</th><th className="num">Recall</th>{'iou' in Object.values(perClass)[0] && <th className="num">IoU</th>}</tr></thead>
        <tbody>
          {CLASSES.map((c) => (
            <tr key={c}>
              <td><span className="swatch" style={{ background: CLASS_COLORS[c] }} />{CLASS_LABELS[c]}</td>
              <td className="num">{frac(perClass[c]?.precision)}</td>
              <td className="num">{frac(perClass[c]?.recall)}</td>
              {'iou' in perClass[c] && <td className="num">{frac(perClass[c]?.iou)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function AgreementTable({ byRef, references, title }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>{title || 'Compared with'}</th><th className="num">Dates</th><th className="num">Agree*</th><th className="num">Correlation r</th><th className="num">Median diff</th><th className="num">Bias</th></tr>
        </thead>
        <tbody>
          {Object.entries(byRef).map(([k, s]) => (
            <tr key={k}>
              <td><span className="swatch" style={{ background: REF_COLORS[k] }} />{references[k]}</td>
              {s ? (
                <>
                  <td className="num">{s.n}</td>
                  <td className="num"><strong>{s.agree_pct}%</strong></td>
                  <td className="num">{s.pearson_r ?? '—'}</td>
                  <td className="num">{ha(s.median_abs_diff_ha)}</td>
                  <td className="num">{s.bias_ha > 0 ? '+' : ''}{ha(s.bias_ha)}</td>
                </>
              ) : <td colSpan={5} className="muted">no comparison</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function CrossCheckChart({ points, references }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={points} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
        <XAxis dataKey="date" tick={{ fontSize: 11 }} />
        <YAxis unit=" ha" width={70} />
        <Tooltip formatter={(v, n) => [ha(v), n]} />
        <Legend />
        <Bar isAnimationActive={false} dataKey="ours_ha" name="AquaRevive measurement" fill={REF_COLORS.ours} />
        {Object.entries(references).map(([k, v]) => <Bar isAnimationActive={false} key={k} dataKey={k} name={v} fill={REF_COLORS[k]} />)}
      </BarChart>
    </ResponsiveContainer>
  )
}
