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
  message:
    'Розмір знімка відрізняється від 512×512. Для гліом такого формату recall у перевірці був ' +
    '≈ 0,51 (проти ≈ 0,99 для 512×512): якщо є підозра на гліому, результат менш надійний.',
}
// A code the interface has no special presentation for.
const OTHER: PredictionWarning = {
  code: 'some_future_warning',
  message: 'Попередження, якого інтерфейс ще не знає.',
}

function renderResult(warnings: PredictionWarning[]) {
  return render(<ResultPanel result={makePrediction({ warnings })} scanUrl="blob:preview" />)
}

function lowConfidenceNotice() {
  return screen.queryByRole('region', { name: 'Низька впевненість моделі' })
}

/** The yellow "Зверніть увагу" list, or null. */
function warningList() {
  return screen.queryByRole('heading', { name: 'Зверніть увагу' })?.closest('section') ?? null
}

function resultHeading() {
  return screen.getByRole('heading', { name: 'Результат' })
}

function follows(first: Node, second: Node) {
  return Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING)
}

describe('low confidence', () => {
  test('is a yellow block above the result', () => {
    renderResult([LOW_CONFIDENCE])

    const notice = lowConfidenceNotice()!
    expect(notice).toBeInTheDocument()
    expect(notice).toHaveClass('bg-amber-50')
    expect(notice.querySelector('[data-icon="warning"]')).toBeInTheDocument()
    expect(notice).toHaveTextContent(
      'Модель недостатньо впевнена в цьому прогнозі. Зверніть увагу: це навчальний проєкт, а не медичний виріб.',
    )
    expect(follows(notice, resultHeading())).toBe(true)
    // Not repeated in the general list of warnings.
    expect(warningList()).not.toBeInTheDocument()
  })

  test('is not shown without warnings', () => {
    renderResult([])

    expect(lowConfidenceNotice()).not.toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(warningList()).not.toBeInTheDocument()
  })
})

describe('unexpected image size', () => {
  test('is a small grey note with an info icon under the result', () => {
    renderResult([SIZE])

    const note = screen.getByRole('note')
    expect(note).toHaveTextContent(SIZE.message)
    expect(note).toHaveClass('text-xs', 'text-slate-500')
    expect(note.querySelector('[data-icon="info"]')).toBeInTheDocument()
    expect(note.querySelector('[data-icon="warning"]')).not.toBeInTheDocument()
    expect(follows(resultHeading(), note)).toBe(true)
  })

  test('is not rendered as a yellow block', () => {
    const { container } = renderResult([SIZE])

    expect(container.querySelector('.bg-amber-50')).not.toBeInTheDocument()
    expect(warningList()).not.toBeInTheDocument()
    expect(lowConfidenceNotice()).not.toBeInTheDocument()
  })
})

describe('other warnings', () => {
  test('stay in the yellow list as before', () => {
    renderResult([OTHER])

    const list = warningList()!
    expect(list).toHaveClass('bg-amber-50')
    expect(within(list).getByText(OTHER.message)).toBeInTheDocument()
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
    expect(lowConfidenceNotice()).not.toBeInTheDocument()
  })

  test('each code goes to its own place when all come together', () => {
    renderResult([SIZE, LOW_CONFIDENCE, OTHER])

    expect(lowConfidenceNotice()).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(SIZE.message)
    const list = warningList()!
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
    expect(within(list).getByText(OTHER.message)).toBeInTheDocument()
    expect(within(list).queryByText(SIZE.message)).not.toBeInTheDocument()
    expect(screen.queryByText(LOW_CONFIDENCE.message)).not.toBeInTheDocument()
  })
})
