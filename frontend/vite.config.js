import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev proxy: the browser only talks to :5173.
//   /api   -> backend  (FastAPI, :8000)
//   /files -> AI agent (FastAPI, :8001) for the rendered satellite / XAI / accuracy images
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.BACKEND_URL || 'http://127.0.0.1:8000',
      '/files': process.env.AGENT_URL || 'http://127.0.0.1:8001',
    },
  },
})
