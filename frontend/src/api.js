// Thin wrapper over the backend REST API (proxied by Vite, see vite.config.js).
// Every number on the site comes from these calls; there is no mock data.
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* non-JSON error body */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body || {}) })

export const api = {
  summary: () => request('/api/summary'),
  geojson: () => request('/api/waterbodies.geojson'),
  waterbodies: () => request('/api/waterbodies'),
  waterbody: (id) => request(`/api/waterbodies/${id}`),
  analyze: (id) => post(`/api/waterbodies/${id}/analyze`),
  priority: () => request('/api/priority'),
  alerts: () => request('/api/alerts'),
  series: (id) => request(`/api/waterbodies/${id}/series`),
  seasonality: (id) => request(`/api/waterbodies/${id}/seasonality`),
  imagery: (id) => request(`/api/waterbodies/${id}/imagery`),
  render: (id, itemId) => post(`/api/waterbodies/${id}/imagery/render`, { item_id: itemId }),
  xai: (id, opts) => post(`/api/waterbodies/${id}/xai`, opts),
  accuracy: (id) => request(`/api/waterbodies/${id}/accuracy`),
  accuracyAll: () => request('/api/accuracy'),
  events: (id) => request(`/api/waterbodies/${id}/events`),
  chat: (message, context, history) => post('/api/chat', { message, context, history }),
  cityEvents: (limit) => request(`/api/events${limit ? `?limit=${limit}` : ''}`),
  reportUrl: (id) => `/api/reports/${id}/pdf`,
}

export const STATUS = {
  persistent_degradation: { label: 'Persistent degradation', color: '#b42318' },
  below_seasonal_normal: { label: 'Below seasonal normal', color: '#dc6803' },
  normal_for_season: { label: 'Normal for season', color: '#15803d' },
  above_seasonal_normal: { label: 'Above seasonal normal', color: '#0b6bcb' },
  normal_too_small: { label: 'Too small to judge', color: '#98a2b3' },
  insufficient_history: { label: 'Not enough history', color: '#98a2b3' },
  stale_data: { label: 'No recent image', color: '#98a2b3' },
  no_measurements: { label: 'Not measured yet', color: '#d0d5dd' },
  not_measured: { label: 'Under 5 ha (skipped)', color: '#e4e7ec' },
}
export const statusOf = (s) => STATUS[s] || { label: s ? s.replaceAll('_', ' ') : 'Unknown', color: '#98a2b3' }

export const CLASS_COLORS = { water: '#0064c8', vegetation: '#28a03c', bare_land: '#c8aa64', built_up: '#dc3c3c' }
export const CLASS_LABELS = { water: 'Water', vegetation: 'Vegetation', bare_land: 'Bare land', built_up: 'Built-up' }
export const REF_COLORS = { ours: '#0b6bcb', dw: '#7a5af8', jrc: '#15803d', s1: '#dc6803' }

export const ha = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)} ha`)
export const pct = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`)
export const frac = (v, d = 1) => (v == null ? '—' : `${(Number(v) * 100).toFixed(d)}%`)
export const b64 = (s) => (s ? `data:image/png;base64,${s}` : null)
