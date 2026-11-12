import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no object URLs; previews only need a stable string.
URL.createObjectURL = vi.fn(() => 'blob:preview')
URL.revokeObjectURL = vi.fn()

afterEach(() => {
  // Vitest globals are off, so Testing Library cannot register its own cleanup.
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  localStorage.clear()
  document.documentElement.className = ''
  document.documentElement.removeAttribute('style')
})
