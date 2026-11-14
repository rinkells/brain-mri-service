import { render, screen, within } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import type { PredictionWarning } from '../../api/types'
import { makePrediction } from '../../test/fixtures'
import { ResultPanel } from './ResultPanel'

const LOW_CONFIDENCE: PredictionWarning = {
  code: 'low_confidence',
  message:
    'Модель недостатньо впевнена в цьому прогнозі: ймовірність передбаченого класу нижча за 90 %.',
}
const SIZE: PredictionWarning = {
  code: 'unexpected_image_size',
  message: 'Розмір знімка 640x480, а не 512x512.',
}

function renderResult(warnings: PredictionWarning[]) {
  render(<ResultPanel result={makePrediction({ warnings })} scanUrl="blob:preview" />)
}

function lowConfidenceNotice() {
  return screen.queryByRole('region', { name: 'Низька впевненість моделі' })
}

describe('low confidence notice', () => {
  test('is shown above the result when warnings contain low_confidence', () => {
    renderResult([LOW_CONFIDENCE])

    const notice = lowConfidenceNotice()
    expect(notice).toBeInTheDocument()
    expect(notice).toHaveClass('bg-amber-50')
    expect(notice).toHaveTextContent(
      'Модель недостатньо впевнена в цьому прогнозі. Зверніть увагу: це навчальний проєкт, а не медичний виріб.',
    )
    const heading = screen.getByRole('heading', { name: 'Результат' })
    expect(notice!.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Not repeated in the general list of warnings.
    expect(screen.queryByRole('heading', { name: 'Зверніть увагу' })).not.toBeInTheDocument()
  })

  test('is not shown without warnings', () => {
    renderResult([])

    expect(lowConfidenceNotice()).not.toBeInTheDocument()
  })

  test('is not shown for other warnings, which stay in the list', () => {
    renderResult([SIZE])

    expect(lowConfidenceNotice()).not.toBeInTheDocument()
    const list = screen.getByRole('heading', { name: 'Зверніть увагу' }).closest('section')!
    expect(within(list).getByText(SIZE.message)).toBeInTheDocument()
  })

  test('comes together with other warnings', () => {
    renderResult([SIZE, LOW_CONFIDENCE])

    expect(lowConfidenceNotice()).toBeInTheDocument()
    const list = screen.getByRole('heading', { name: 'Зверніть увагу' }).closest('section')!
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText(SIZE.message)).toBeInTheDocument()
    expect(screen.queryByText(LOW_CONFIDENCE.message)).not.toBeInTheDocument()
  })
})
