import { useEffect } from 'react'
import { GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'
import { useNavigate } from 'react-router-dom'
import { statusOf } from '../api.js'

const BENGALURU = [12.97, 77.6]

// Outlines arrive after the map mounts, so zoom once they are known.
function FitTo({ bounds }) {
  const map = useMap()
  const key = bounds ? bounds.flat().join(',') : ''
  useEffect(() => { if (bounds) map.fitBounds(bounds, { padding: [30, 30] }) }, [key])   // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

// All OpenStreetMap lake outlines, coloured by their latest measured status.
export default function LakesMap({ data, focusId, height = 520 }) {
  const navigate = useNavigate()
  const focus = focusId && data?.features.find((f) => f.properties.id === focusId)
  const b = focus?.properties.bbox
  const bounds = b ? [[b[1], b[0]], [b[3], b[2]]] : null

  return (
    <div className="map" style={{ height }}>
      <MapContainer
        center={BENGALURU}
        zoom={10}
        scrollWheelZoom={false}
        style={{ height: '100%' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitTo bounds={bounds} />
        {data && (
          <GeoJSON
            key={focusId || 'all'}
            data={data}
            style={(f) => {
              const s = statusOf(f.properties.status)
              const isFocus = f.properties.id === focusId
              return { color: isFocus ? '#111' : s.color, weight: isFocus ? 3 : 1.2, fillColor: s.color, fillOpacity: 0.55 }
            }}
            onEachFeature={(f, layer) => {
              const p = f.properties
              const s = statusOf(p.status)
              layer.bindTooltip(
                `<strong>${p.name || 'Unnamed lake'}</strong><br/>${s.label}` +
                (p.open_water_ha != null ? `<br/>Open water ${p.open_water_ha} ha (${p.date})` : '') +
                `<br/>Outline ${p.outline_ha} ha`,
                { sticky: true },
              )
              if (p.id) layer.on('click', () => navigate(`/lake/${p.id}`))
            }}
          />
        )}
      </MapContainer>
    </div>
  )
}
