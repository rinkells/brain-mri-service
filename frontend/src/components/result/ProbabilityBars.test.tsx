import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { formatPercent } from '../../lib/format'
import { ProbabilityBars } from './ProbabilityBars'

const probabilities = { glioma: 0.05, meningioma: 0.8, notumor: 0.12, pituitary: 0.03 }

test('shows one bar per class in model order', () => {
  render(<ProbabilityBars probabilities={probabilities} predicted="meningioma" />)

  const meters = screen.getAllByRole('meter')

  expect(meters.map((meter) => meter.getAttribute('aria-label'))).toEqual([
    'Гліома',
    'Менінгіома',
    'Пухлини не виявлено',
    'Пухлина гіпофіза',
  ])
})

test('bar width and value follow the probability', () => {
  render(<ProbabilityBars probabilities={probabilities} predicted="meningioma" />)

  const meter = screen.getByRole('meter', { name: 'Менінгіома' })

  expect(meter).toHaveAttribute('aria-valuenow', '80')
  expect(meter).toHaveAttribute('aria-valuetext', formatPercent(0.8))
  expect(meter.firstElementChild).toHaveStyle({ width: '80%' })
  expect(screen.getByText(formatPercent(0.8))).toBeInTheDocument()
})

test('treats a missing class as zero', () => {
  render(<ProbabilityBars probabilities={{ glioma: 1 }} predicted="glioma" />)

  expect(screen.getByRole('meter', { name: 'Пухлина гіпофіза' })).toHaveAttribute(
    'aria-valuenow',
    '0',
  )
})
