/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Upload limit checked in the browser; keep it equal to the backend's MAX_UPLOAD_MB. */
  readonly VITE_MAX_UPLOAD_MB?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
