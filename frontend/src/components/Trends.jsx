import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { CLASS_COLORS, ha } from '../api.js'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const year = (t) => new Date(t).getFullYear()
const day = (t) => new Date(t).toISOString().slice(0, 10)

export default function Trends({ series, seasonality }) {
  // one array for every series (separate data arrays break shared axis scales)
  const points = (series?.points || []).map((p) => ({
    ...p, t: Date.parse(p.date), cloudy: p.status === 'cloudy' ? 0 : null,
  }))
  const ok = points.filter((p) => p.open_water_ha != null)
  const cloudy = points.filter((p) => p.cloudy != null)
  const cp = seasonality?.change_point
  const years = ok.length ? [...new Set(points.map((p) => year(p.t)))] : []
  const ticks = years.map((y) => Date.parse(`${y}-01-01`)).filter((t) => t >= points[0].t)
  const monthly = (seasonality?.monthly_normal || []).map((m) => ({ ...m, name: MONTHS[m.month - 1], range: [m.low_ha, m.high_ha] }))
  const yearly = seasonality?.yearly || []

  return (
    <div className="stack">
      <div className="card">
        <h2>Open water, every measured date</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          {ok.length} clear measurements and {cloudy.length} cloudy dates (grey ticks) between {ok[0]?.date} and {ok.at(-1)?.date}.
          The dashed line is this lake's normal for that time of year.
        </p>
        <ResponsiveContainer width="100%" height={340}>
          <ComposedChart data={points} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
            <XAxis dataKey="t" type="number" scale="time" domain={['dataMin', 'dataMax']} ticks={ticks} tickFormatter={year} />
            <YAxis unit=" ha" width={70} />
            <Tooltip labelFormatter={day} formatter={(v, n) => [ha(v), n]} />
            <Legend />
            {series?.outline_ha && <ReferenceLine y={series.outline_ha} stroke="#999" strokeDasharray="2 4"
              label={{ value: `outline ${Math.round(series.outline_ha)} ha`, position: 'insideTopRight', fontSize: 11 }} />}
            <Area isAnimationActive={false} type="monotone" dataKey="vegetation_ha" connectNulls name="Green growth inside outline" stroke={CLASS_COLORS.vegetation}
                  fill={CLASS_COLORS.vegetation} fillOpacity={0.15} strokeWidth={1} dot={false} />
            <Line isAnimationActive={false} type="monotone" dataKey="normal_ha" name="Normal for the season" stroke="#555" strokeDasharray="5 4" dot={false} connectNulls />
            <Line isAnimationActive={false} type="monotone" dataKey="open_water_ha" name="Open water" stroke={CLASS_COLORS.water} strokeWidth={2} dot={{ r: 2.5 }} connectNulls />
            <Line isAnimationActive={false} dataKey="cloudy" name="Cloudy (no measurement)" stroke="none" dot={{ r: 3, fill: '#bbb', stroke: '#bbb' }} legendType="square" />
            {cp?.change_point_year && <ReferenceLine x={Date.parse(`${cp.change_point_year}-01-01`)} stroke="#b42318" strokeDasharray="4 4"
              label={{ value: `change ${cp.change_point_year}`, position: 'top', fontSize: 11 }} />}
          </ComposedChart>
        </ResponsiveContainer>
        {cp?.narrative && <p className="small" style={{ marginBottom: 0 }}>{cp.narrative}</p>}
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2>Seasonal pattern</h2>
          <p className="muted small" style={{ marginTop: 0 }}>Median open water per month across years (band = lowest–highest year).</p>
          {monthly.length ? (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={monthly} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
                <XAxis dataKey="name" />
                <YAxis unit=" ha" width={70} />
                <Tooltip formatter={(v, n) => [Array.isArray(v) ? `${v[0]}–${v[1]} ha` : ha(v), n]} />
                <Area isAnimationActive={false} dataKey="range" name="Range across years" stroke="none" fill={CLASS_COLORS.water} fillOpacity={0.15} />
                <Line isAnimationActive={false} dataKey="normal_ha" name="Normal" stroke={CLASS_COLORS.water} strokeWidth={2} />
              </ComposedChart>
            </ResponsiveContainer>
          ) : <p className="muted small">Needs the same month in at least two years.</p>}
          <p className="muted small" style={{ marginBottom: 0 }}>{seasonality?.note}</p>
        </div>

        <div className="card">
          <h2>Year by year</h2>
          <p className="muted small" style={{ marginTop: 0 }}>Driest point before the monsoon vs fullest after it.</p>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={yearly} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3e9f0" />
              <XAxis dataKey="year" />
              <YAxis unit=" ha" width={70} />
              <Tooltip formatter={(v, n) => [ha(v), n]} />
              <Legend />
              <Bar isAnimationActive={false} dataKey="pre_monsoon_median_ha" name="Pre-monsoon (Mar–May)" fill="#c8aa64" />
              <Bar isAnimationActive={false} dataKey="post_monsoon_max_ha" name="Post-monsoon max (Oct–Jan)" fill={CLASS_COLORS.water} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
