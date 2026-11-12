const DEFAULT_MAX_UPLOAD_MB = 10

function readMaxUploadMb(): number {
  const value = Number(import.meta.env.VITE_MAX_UPLOAD_MB)
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_MAX_UPLOAD_MB
}

/** Same default as MAX_UPLOAD_MB in the backend. */
export const MAX_UPLOAD_MB = readMaxUploadMb()
export const MAX_UPLOAD_BYTES = Math.floor(MAX_UPLOAD_MB * 1024 * 1024)

/** How often the service status is re-checked. */
export const HEALTH_POLL_MS = 15_000
