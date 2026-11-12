import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// FastAPI dev server. The browser only talks to Vite, which forwards these
// paths, so the backend needs no CORS setup.
const BACKEND_URL = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Keys are path prefixes. List every API path explicitly, even where one is a
    // prefix of another (/predict and /predictions): a path missing here gets
    // Vite's index.html instead of JSON.
    proxy: {
      '/health': BACKEND_URL,
      '/predict': BACKEND_URL,
      '/predictions': BACKEND_URL,
      '/stats': BACKEND_URL,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
