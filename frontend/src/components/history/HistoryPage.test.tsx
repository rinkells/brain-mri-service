import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import type { PredictionRecord } from '../../api/types'
import { formatDateTime, formatMilliseconds, formatPercent } from '../../lib/format'
import {
  callsTo,
  jsonResponse,
  makePage,
  makeRecord,
  makeStats,
  mockApi,
  queryOf,
} from '../../test/fixtures'
import { HistoryPage } from './HistoryPage'

const EMPTY_STATS = makeStats({
  total: 0,
  by_class: { glioma: 0, meningioma: 0, notumor: 0, pituitary: 0 },
  with_warnings_share: null,
  average_latency_ms: null,
})

/** A fake history of `total` records served page by page, newest first. */
function serveHistory(records: PredictionRecord[]) {
  let current = [...records]
  return {
    remove(id: string) {
      current = current.filter((record) => record.id !== id)
    },
    handler: (url: string) => {
      const { limit = '10', offset = '0', predicted_class } = queryOf(url)
      const matching = predicted_class
        ? current.filter((record) => record.predicted_class === predicted_class)
        : current
      const start = Number(offset)
      return jsonResponse(
        makePage(matching.slice(start, start + Number(limit)), {
          total: matching.length,
          limit: Number(limit),
          offset: start,
        }),
      )
    },
  }
}

function newestFirst(
  count: number,
  overrides: (n: number) => Partial<PredictionRecord> = () => ({}),
) {
  return Array.from({ length: count }, (_, index) => {
    const n = count - index
    return makeRecord(n, overrides(n))
  })
}

function rows() {
  // The first row is the header.
  return screen.getAllByRole('row').slice(1)
}

describe('statistics', () => {
  test('shows totals, share with warnings, latency and classes', async () => {
    mockApi({
      predictions: () => jsonResponse(makePage([])),
      stats: () => jsonResponse(makeStats()),
    })

    render(<HistoryPage />)

    const total = await screen.findByText('Усього прогнозів')
    expect(total.closest('div')).toHaveTextContent('4')
    expect(screen.getByText('З попередженнями').closest('div')).toHaveTextContent(
      formatPercent(0.25),
    )
    expect(screen.getByText('Середній час обробки').closest('div')).toHaveTextContent(
      formatMilliseconds(154.5),
    )
    const distribution = screen.getByRole('list', { name: 'Розподіл за класами' })
    expect(within(distribution).getByText('Гліома').closest('li')).toHaveTextContent('2')
    expect(within(distribution).getByText('Пухлина гіпофіза').closest('li')).toHaveTextContent('0')
  })

  test('shows dashes when there are no predictions yet', async () => {
    mockApi({
      predictions: () => jsonResponse(makePage([])),
      stats: () => jsonResponse(EMPTY_STATS),
    })

    render(<HistoryPage />)

    expect((await screen.findByText('Усього прогнозів')).closest('div')).toHaveTextContent('0')
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
})

describe('table', () => {
  test('shows time, class, confidence, size and warnings for each prediction', async () => {
    const records = [
      makeRecord(2, {
        predicted_class: 'notumor',
        image_size: { width: 640, height: 480 },
        warnings: [{ code: 'unexpected_image_size', message: 'Розмір знімка 640x480.' }],
      }),
      makeRecord(1, { predicted_class: 'glioma' }),
    ]
    mockApi({
      predictions: () => jsonResponse(makePage(records)),
      stats: () => jsonResponse(makeStats()),
    })

    render(<HistoryPage />)

    await screen.findByText('Пухлини не виявлено')
    const [first, second] = rows()
    const firstCells = within(first)
      .getAllByRole('cell')
      .map((cell) => cell.textContent)
    expect(firstCells.slice(0, 5)).toEqual([
      formatDateTime(records[0].created_at),
      'Пухлини не виявлено',
      formatPercent(0.7),
      '640×480',
      'Так',
    ])
    expect(within(first).getByText('Так')).toHaveAttribute('title', 'Розмір знімка 640x480.')
    expect(within(second).getByText('Гліома')).toBeInTheDocument()
    expect(within(second).getByText('Ні')).toBeInTheDocument()
  })

  test('asks for the first ten records', async () => {
    const fetchMock = mockApi({
      predictions: () => jsonResponse(makePage([])),
      stats: () => jsonResponse(EMPTY_STATS),
    })

    render(<HistoryPage />)

    await screen.findByText(/Історія порожня/)
    expect(queryOf(callsTo(fetchMock, '/predictions')[0][0])).toEqual({ limit: '10', offset: '0' })
  })

  test('explains an empty history', async () => {
    mockApi({
      predictions: () => jsonResponse(makePage([])),
      stats: () => jsonResponse(EMPTY_STATS),
    })

    render(<HistoryPage />)

    expect(
      await screen.findByText('Історія порожня. Проаналізуйте знімок на вкладці «Аналіз».'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Сторінки історії' })).not.toBeInTheDocument()
  })
})

describe('pagination', () => {
  test('moves between pages of ten', async () => {
    const history = serveHistory(newestFirst(25))
    const fetchMock = mockApi({
      predictions: history.handler,
      stats: () => jsonResponse(makeStats()),
    })
    const user = userEvent.setup()
    render(<HistoryPage />)

    expect(await screen.findByText('Показано 1–10 з 25')).toBeInTheDocument()
    expect(rows()).toHaveLength(10)
    expect(screen.getByRole('button', { name: 'Попередня' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Наступна' }))
    expect(await screen.findByText('Показано 11–20 з 25')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Наступна' }))
    expect(await screen.findByText('Показано 21–25 з 25')).toBeInTheDocument()
    expect(rows()).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Наступна' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Попередня' }))
    expect(await screen.findByText('Показано 11–20 з 25')).toBeInTheDocument()

    const offsets = callsTo(fetchMock, '/predictions').map(([url]) => queryOf(url).offset)
    expect(offsets).toEqual(['0', '10', '20', '10'])
  })
})

describe('class filter', () => {
  test('asks the server for one class and starts from the first page', async () => {
    const records = newestFirst(15, (n) => ({
      predicted_class: n % 3 === 0 ? 'glioma' : 'notumor',
    }))
    const history = serveHistory(records)
    const fetchMock = mockApi({
      predictions: history.handler,
      stats: () => jsonResponse(makeStats()),
    })
    const user = userEvent.setup()
    render(<HistoryPage />)
    await screen.findByText('Показано 1–10 з 15')
    await user.click(screen.getByRole('button', { name: 'Наступна' }))
    await screen.findByText('Показано 11–15 з 15')

    await user.selectOptions(screen.getByLabelText('Клас'), 'Гліома')

    expect(await screen.findByText('Показано 1–5 з 5')).toBeInTheDocument()
    expect(rows().every((row) => within(row).queryByText('Гліома'))).toBe(true)
    const last = callsTo(fetchMock, '/predictions').at(-1)!
    expect(queryOf(last[0])).toEqual({ limit: '10', offset: '0', predicted_class: 'glioma' })
  })

  test('says when a class has no predictions', async () => {
    const history = serveHistory(newestFirst(3, () => ({ predicted_class: 'glioma' })))
    mockApi({ predictions: history.handler, stats: () => jsonResponse(makeStats()) })
    const user = userEvent.setup()
    render(<HistoryPage />)
    await screen.findByText('Показано 1–3 з 3')

    await user.selectOptions(screen.getByLabelText('Клас'), 'pituitary')

    expect(await screen.findByText('Немає прогнозів класу «Пухлина гіпофіза».')).toBeInTheDocument()
  })
})

describe('deleting a record', () => {
  function setup(records: PredictionRecord[], deleteStatus = 204) {
    const history = serveHistory(records)
    const fetchMock = mockApi({
      predictions: history.handler,
      stats: () => jsonResponse(makeStats()),
      deletePrediction: (url) => {
        const id = url.split('/').at(-1)!
        if (deleteStatus === 204) {
          history.remove(id)
          return new Response(null, { status: 204 })
        }
        return jsonResponse({ detail: 'Прогноз не знайдено.' }, deleteStatus)
      },
    })
    return { fetchMock, user: userEvent.setup() }
  }

  test('asks for confirmation, deletes and refreshes the list and statistics', async () => {
    const records = newestFirst(2)
    const { fetchMock, user } = setup(records)
    render(<HistoryPage />)
    await screen.findByText('Показано 1–2 з 2')
    const statsRequests = callsTo(fetchMock, '/stats').length

    await user.click(
      screen.getByRole('button', {
        name: `Видалити запис від ${formatDateTime(records[0].created_at)}`,
      }),
    )
    expect(screen.getByText('Видалити?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Так' }))

    expect(await screen.findByText('Показано 1–1 з 1')).toBeInTheDocument()
    const deletes = callsTo(fetchMock, '/predictions', 'DELETE')
    expect(deletes.map(([url]) => url)).toEqual([`/predictions/${records[0].id}`])
    await waitFor(() => expect(callsTo(fetchMock, '/stats').length).toBe(statsRequests + 1))
    expect(screen.queryByText('Видалити?')).not.toBeInTheDocument()
  })

  test('can be cancelled', async () => {
    const records = newestFirst(1)
    const { fetchMock, user } = setup(records)
    render(<HistoryPage />)
    await screen.findByText('Показано 1–1 з 1')

    await user.click(screen.getByRole('button', { name: /^Видалити запис від/ }))
    await user.click(screen.getByRole('button', { name: 'Ні' }))

    expect(callsTo(fetchMock, '/predictions', 'DELETE')).toHaveLength(0)
    expect(screen.getByRole('button', { name: /^Видалити запис від/ })).toBeInTheDocument()
  })

  test('removing the only record on the last page goes back a page', async () => {
    const records = newestFirst(11)
    const { user } = setup(records)
    render(<HistoryPage />)
    await screen.findByText('Показано 1–10 з 11')
    await user.click(screen.getByRole('button', { name: 'Наступна' }))
    await screen.findByText('Показано 11–11 з 11')

    await user.click(screen.getByRole('button', { name: /^Видалити запис від/ }))
    await user.click(screen.getByRole('button', { name: 'Так' }))

    expect(await screen.findByText('Показано 1–10 з 10')).toBeInTheDocument()
  })

  test('a record that is already gone is reported and the list is refreshed', async () => {
    const records = newestFirst(1)
    const { fetchMock, user } = setup(records, 404)
    render(<HistoryPage />)
    await screen.findByText('Показано 1–1 з 1')
    const listRequests = callsTo(fetchMock, '/predictions').length

    await user.click(screen.getByRole('button', { name: /^Видалити запис від/ }))
    await user.click(screen.getByRole('button', { name: 'Так' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Запис не знайдено')
    expect(alert).toHaveTextContent('Прогноз не знайдено.')
    await waitFor(() => expect(callsTo(fetchMock, '/predictions').length).toBe(listRequests + 1))
  })
})

describe('history unavailable', () => {
  test.each([
    ['database is down', 'База даних недоступна. Спробуйте пізніше.'],
    ['history is disabled', 'Історію вимкнено: не задано DATABASE_URL.'],
  ])('shows the reason when the %s', async (_, detail) => {
    mockApi({
      predictions: () => jsonResponse({ detail }, 503),
      stats: () => jsonResponse({ detail }, 503),
    })

    render(<HistoryPage />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Історія недоступна')
    expect(alert).toHaveTextContent(detail)
    expect(await screen.findByText('Статистика недоступна.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  test('reports an unreachable server', async () => {
    mockApi({
      predictions: () => Promise.reject(new TypeError('Failed to fetch')),
      stats: () => Promise.reject(new TypeError('Failed to fetch')),
    })

    render(<HistoryPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Сервер недоступний')
  })
})
