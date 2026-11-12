import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { HeatmapView } from './HeatmapView'

test('shows the scan and the heat map side by side', () => {
  render(<HeatmapView scanUrl="blob:scan" heatmapBase64="iVBORw0KGgo=" />)

  expect(screen.getByRole('img', { name: 'Завантажений знімок' })).toHaveAttribute(
    'src',
    'blob:scan',
  )
  expect(screen.getByRole('img', { name: 'Теплова карта Grad-CAM' })).toHaveAttribute(
    'src',
    'data:image/png;base64,iVBORw0KGgo=',
  )
})

test('explains how to get a heat map when none was requested', () => {
  render(<HeatmapView scanUrl="blob:scan" heatmapBase64={null} />)

  expect(screen.queryByRole('img', { name: 'Теплова карта Grad-CAM' })).not.toBeInTheDocument()
  expect(screen.getByText(/Теплову карту не запитували/)).toBeInTheDocument()
})
