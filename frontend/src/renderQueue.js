import { useEffect, useState } from 'react'
import { api } from './api.js'

// First-time renders download a real Sentinel-2 image (~10-30 s each), so run
// at most two at once and remember results for the whole session.
const MAX_PARALLEL = 2
const cache = new Map()
const waiting = []
let running = 0

function pump() {
  while (running < MAX_PARALLEL && waiting.length) {
    const job = waiting.shift()
    running++
    job.run().finally(() => { running--; pump() })
  }
}

export function renderImage(lakeId, itemId) {
  const key = `${lakeId}|${itemId}`
  if (!cache.has(key)) {
    cache.set(key, new Promise((resolve, reject) => {
      waiting.push({ run: () => api.render(lakeId, itemId).then(resolve, (e) => { cache.delete(key); reject(e) }) })
      pump()
    }))
  }
  return cache.get(key)
}

export function useRender(lakeId, itemId) {
  const [state, setState] = useState({ data: null, error: null })
  useEffect(() => {
    if (!itemId) return
    let alive = true
    setState({ data: null, error: null })
    renderImage(lakeId, itemId).then(
      (data) => alive && setState({ data, error: null }),
      (error) => alive && setState({ data: null, error: error.message }),
    )
    return () => { alive = false }
  }, [lakeId, itemId])
  return state
}
