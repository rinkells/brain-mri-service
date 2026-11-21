import { render, screen } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'

import { validateImageFile } from '../../lib/validation'
import { makeFile } from '../../test/fixtures'
import { UploadZone } from './UploadZone'

// VITE_MAX_UPLOAD_MB=2.5: a limit that is not a whole number of megabytes.
vi.mock('../../lib/config', () => ({
  MAX_UPLOAD_MB: 2.5,
  MAX_UPLOAD_BYTES: Math.floor(2.5 * 1024 * 1024),
  HEALTH_POLL_MS: 15_000,
}))

describe('size limit hint', () => {
  test('is written like the error message, with a decimal comma', () => {
    render(<UploadZone selection={null} error={null} onFile={() => {}} onClear={() => {}} />)

    expect(screen.getByText('JPEG або PNG, до 2,5 МБ')).toBeInTheDocument()
    expect(validateImageFile(makeFile('a.png', 'image/png', 3 * 1024 * 1024))).toContain(
      'Максимум — 2,5 МБ',
    )
  })
})
