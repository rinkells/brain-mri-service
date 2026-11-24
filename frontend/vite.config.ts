import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// FastAPI dev server. The browser only talks to Vite, which forwards these
// paths, so the backend needs no CORS setup.
const BACKEND_URL = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The proxy passes the browser's Origin on, and the backend accepts POST and
  // DELETE only from the origins in ALLOWED_ORIGINS (ports 5173 and 4173 by
  // default). strictPort stops Vite with an error instead of moving to another
  // port, where the UI could read but not analyse or delete.
  preview: { strictPort: true },
  server: {
    strictPort: true,
    // Keys are path prefixes. List every API path explicitly, even where one is a
    // prefix of another (/predict and /predictions, /health and /health/ready): a
    // path missing here gets Vite's index.html instead of JSON. nginx.conf forwards
    // the same paths (backend/tests/test_deployment.py compares them).
    proxy: {
      '/health': BACKEND_URL,
      '/health/ready': BACKEND_URL,
      '/predict': BACKEND_URL,
      '/predictions': BACKEND_URL,
      '/stats': BACKEND_URL,
      '/auth': BACKEND_URL,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
