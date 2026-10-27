import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import StatusPill from '../components/StatusPill.jsx'
import Overview from '../components/Overview.jsx'
import YearlyImagery from '../components/YearlyImagery.jsx'
import Trends from '../components/Trends.jsx'
import Explain from '../components/Explain.jsx'
import LakeAccuracy from '../components/LakeAccuracy.jsx'
import CausesPriority from '../components/CausesPriority.jsx'
import ChangeEvents from '../components/ChangeEvents.jsx'

const TABS = {
  overview: 'Overview',
  years: 'Satellite images by year',
  trends: 'Trends',
  events: 'Change events',
  explain: 'Explainability (XAI)',
  accuracy: 'Accuracy',
  causes: 'Causes & priority',
}

export default function LakePage() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = TABS[params.get('tab')] ? params.get('tab') : 'overview'
  const setTab = (k) => setParams(k === 'overview' ? {} : { tab: k }, { replace: true })
  const [wb, setWb] = useState(null)
  const [a, setA] = useState(null)
  const [imagery, setImagery] = useState(null)
  const [series, setSeries] = useState(null)
  const [seasonality, setSeasonality] = useState(null)
  const [acc, setAcc] = useState(null)
  const [events, setEvents] = useState(null)
  const [geo, setGeo] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setA(null); setImagery(null); setSeries(null); setSeasonality(null); setAcc(null); setEvents(null); setError(null)
    const fail = (e) => setError(e.message)
    api.waterbody(id).then(setWb, fail)
    api.analyze(id).then(setA, fail)
    api.imagery(id).then(setImagery, () => setImagery({ years: [] }))
    api.series(id).then(setSeries, () => setSeries({ points: [] }))
    api.seasonality(id).then(setSeasonality, () => setSeasonality(null))
    api.accuracy(id).then(setAcc, () => setAcc(null))
    api.events(id).then(setEvents, (e) => setEvents({ detail: e.message }))
    api.geojson().then(setGeo, () => {})
  }, [id])

  return (
    <div className="stack">
      <div className="row">
        <div>
          <Link to="/" className="small">← All lakes</Link>
          <h1>{wb?.name || '…'}</h1>
          <div className="row small muted" style={{ gap: '0.5rem' }}>
            {a?.anomaly?.change_type && <StatusPill status={a.anomaly.change_type} />}
            {imagery && <span>{imagery.outline_ha} ha outline · {imagery.years.filter((y) => !y.missing).length} years of clear images</span>}
          </div>
        </div>
      </div>
      {error && <div className="banner danger">{error}</div>}
      <nav className="tabs">
        {Object.entries(TABS).map(([k, label]) => (
          <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{label}</button>
        ))}
      </nav>
      {!a ? <p className="muted"><span className="spinner" />Loading the lake's measurements…</p> : (
        <>
          {tab === 'overview' && <Overview id={id} a={a} imagery={imagery} geo={geo} />}
          {tab === 'years' && (imagery ? <YearlyImagery id={id} imagery={imagery} changeYear={a.change_point?.change_point_year} /> : <p className="muted">Loading…</p>)}
          {tab === 'trends' && <Trends series={series} seasonality={seasonality} />}
          {tab === 'events' && <ChangeEvents id={id} data={events} />}
          {tab === 'explain' && <Explain id={id} imagery={imagery} />}
          {tab === 'accuracy' && <LakeAccuracy acc={acc} />}
          {tab === 'causes' && <CausesPriority id={id} a={a} />}
        </>
      )}
    </div>
  )
}
