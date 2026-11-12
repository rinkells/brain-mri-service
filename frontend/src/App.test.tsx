import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'

import App from './App'
import { formatPercent } from './lib/format'
import { jsonResponse, makeFile, makePrediction, mockApi, predictCalls } from './test/fixtures'

const SIZE_WARNING =
  'Розмір знімка 640x480, а не 512x512. На знімках іншого розміру модель працює гірше.'

function analyzeButton() {
  return screen.getByRole('button', { name: 'Проаналізувати' })
}

function fileInput() {
  return screen.getByLabelText('Файл знімка МРТ')
}

/** Render the app, choose a scan and submit it. */
async function analyze(options: { gradcam?: boolean; file?: File } = {}) {
  const user = userEvent.setup()
  render(<App />)
  await user.upload(fileInput(), options.file ?? makeFile())
  if (options.gradcam) {
    await user.click(screen.getByRole('switch', { name: 'Показати теплову карту' }))
  }
  await user.click(analyzeButton())
  return user
}

describe('initial render', () => {
  test('shows the title, an empty upload zone and a disabled button', async () => {
    mockApi()

    render(<App />)

    expect(screen.getByRole('heading', { name: 'Класифікація МРТ мозку' })).toBeInTheDocument()
    expect(screen.getByText(/Перетягніть знімок сюди/)).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Показати теплову карту' })).not.toBeChecked()
    expect(analyzeButton()).toBeDisabled()
    expect(screen.getByText(/не медичний виріб/)).toBeInTheDocument()
    await screen.findByText('Сервіс працює')
  })

  test('shows the device when the service is up', async () => {
    mockApi()

    render(<App />)

    expect(await screen.findByRole('status')).toHaveTextContent('Сервіс працює · cpu')
  })

  test('reports an unreachable service', async () => {
    mockApi({ health: () => Promise.reject(new TypeError('Failed to fetch')) })

    render(<App />)

    expect(await screen.findByText('Сервіс недоступний')).toBeInTheDocument()
  })

  test('reports the service as down when the dev proxy answers 502', async () => {
    mockApi({ health: () => new Response('', { status: 502 }) })

    render(<App />)

    expect(await screen.findByText('Сервіс недоступний')).toBeInTheDocument()
  })
})

describe('file validation before upload', () => {
  test('a valid scan gets a preview and enables the button', async () => {
    mockApi()
    const user = userEvent.setup()
    render(<App />)

    await user.upload(fileInput(), makeFile('brain.png', 'image/png', 1536 * 1024))

    expect(screen.getByRole('img', { name: 'Прев’ю вибраного знімка' })).toHaveAttribute(
      'src',
      'blob:preview',
    )
    expect(screen.getByText('brain.png')).toBeInTheDocument()
    expect(screen.getByText(/1,5 МБ/)).toBeInTheDocument()
    expect(analyzeButton()).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  test('a file of another format is refused without contacting the server', async () => {
    const fetchMock = mockApi()
    // The OS dialog lets the user pick "All files", bypassing the accept filter.
    const user = userEvent.setup({ applyAccept: false })
    render(<App />)

    await user.upload(fileInput(), makeFile('scan.gif', 'image/gif'))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Файл «scan.gif» не підходить: потрібен знімок у форматі JPEG або PNG.',
    )
    expect(screen.queryByRole('img', { name: /Прев’ю/ })).not.toBeInTheDocument()
    expect(analyzeButton()).toBeDisabled()
    expect(predictCalls(fetchMock)).toHaveLength(0)
  })

  test('a file over the size limit is refused', async () => {
    mockApi()
    const user = userEvent.setup()
    render(<App />)

    await user.upload(fileInput(), makeFile('big.png', 'image/png', 11 * 1024 * 1024))

    expect(screen.getByRole('alert')).toHaveTextContent('Файл завеликий (11 МБ). Максимум — 10 МБ.')
    expect(analyzeButton()).toBeDisabled()
  })

  test('a file can be dropped onto the zone', async () => {
    mockApi()
    render(<App />)

    fireEvent.drop(screen.getByTestId('drop-zone'), {
      dataTransfer: { files: [makeFile('dropped.jpg', 'image/jpeg')] },
    })

    expect(await screen.findByText('dropped.jpg')).toBeInTheDocument()
    expect(analyzeButton()).toBeEnabled()
  })

  test('a dropped file of another format is refused', async () => {
    mockApi()
    render(<App />)

    fireEvent.drop(screen.getByTestId('drop-zone'), {
      dataTransfer: { files: [makeFile('report.pdf', 'application/pdf')] },
    })

    expect(await screen.findByRole('alert')).toHaveTextContent('«report.pdf» не підходить')
  })

  test('the chosen file can be removed', async () => {
    mockApi()
    const user = userEvent.setup()
    render(<App />)
    await user.upload(fileInput(), makeFile())

    await user.click(screen.getByRole('button', { name: 'Прибрати' }))

    expect(screen.queryByRole('img', { name: /Прев’ю/ })).not.toBeInTheDocument()
    expect(analyzeButton()).toBeDisabled()
  })
})

describe('successful analysis', () => {
  test('shows a loading state while the request is running', async () => {
    let respond: (response: Response) => void = () => {}
    mockApi({ predict: () => new Promise<Response>((resolve) => (respond = resolve)) })

    await analyze()

    const button = screen.getByRole('button', { name: 'Аналізуємо…' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Модель аналізує знімок…')).toBeInTheDocument()

    respond(jsonResponse(makePrediction()))

    expect(await screen.findByTestId('predicted-class')).toHaveTextContent('Менінгіома')
    expect(analyzeButton()).toBeEnabled()
  })

  test('shows the class, four probability bars and the scan', async () => {
    const fetchMock = mockApi({ predict: () => jsonResponse(makePrediction()) })

    await analyze()

    expect(await screen.findByTestId('predicted-class')).toHaveTextContent('Менінгіома')
    expect(screen.getByText(`ймовірність ${formatPercent(0.8)}`, { exact: false })).toBeVisible()
    const meters = screen.getAllByRole('meter')
    expect(meters).toHaveLength(4)
    expect(meters.map((meter) => meter.getAttribute('aria-valuenow'))).toEqual([
      '5',
      '80',
      '12',
      '3',
    ])
    expect(screen.getByRole('img', { name: 'Завантажений знімок' })).toHaveAttribute(
      'src',
      'blob:preview',
    )
    expect(screen.getByText(/Теплову карту не запитували/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Зверніть увагу' })).not.toBeInTheDocument()
    expect(predictCalls(fetchMock)[0][0]).toBe('/predict')
  })

  test('requests and shows the heat map when the switch is on', async () => {
    const fetchMock = mockApi({
      predict: () => jsonResponse(makePrediction({ gradcam_png_base64: 'iVBORw0KGgo=' })),
    })

    await analyze({ gradcam: true })

    expect(await screen.findByRole('img', { name: 'Теплова карта Grad-CAM' })).toHaveAttribute(
      'src',
      'data:image/png;base64,iVBORw0KGgo=',
    )
    expect(predictCalls(fetchMock)[0][0]).toBe('/predict?gradcam=true')
  })

  test('shows warnings in a highlighted block', async () => {
    mockApi({
      predict: () =>
        jsonResponse(
          makePrediction({
            image_size: { width: 640, height: 480 },
            warnings: [{ code: 'unexpected_image_size', message: SIZE_WARNING }],
          }),
        ),
    })

    await analyze()

    const heading = await screen.findByRole('heading', { name: 'Зверніть увагу' })
    const block = heading.closest('section')!
    expect(block).toHaveClass('bg-amber-50')
    expect(within(block).getByText(SIZE_WARNING)).toBeInTheDocument()
    expect(screen.getByText(/знімок 640×480/)).toBeInTheDocument()
  })

  test('choosing another file clears the previous result', async () => {
    mockApi({ predict: () => jsonResponse(makePrediction()) })
    const user = await analyze()
    await screen.findByTestId('predicted-class')

    await user.upload(fileInput(), makeFile('next.png'))

    expect(screen.queryByTestId('predicted-class')).not.toBeInTheDocument()
    expect(screen.getByText(/Тут з’явиться результат/)).toBeInTheDocument()
  })
})

describe('errors from the server', () => {
  test.each([
    [400, 'Файл не вдалося прочитати', 'Файл не є зображенням.'],
    [413, 'Файл завеликий', 'Файл завеликий: дозволено не більше 10 МБ.'],
    [415, 'Непідтримуваний формат', 'Формат GIF не підтримується: потрібен JPEG або PNG.'],
  ])('%i shows "%s" with the server explanation', async (status, title, detail) => {
    mockApi({ predict: () => jsonResponse({ detail }, status) })

    await analyze()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(title)
    expect(alert).toHaveTextContent(detail)
    expect(screen.queryByTestId('predicted-class')).not.toBeInTheDocument()
  })

  test('422 shows a readable message instead of the raw validation list', async () => {
    mockApi({
      predict: () =>
        jsonResponse(
          { detail: [{ loc: ['body', 'file'], msg: 'Field required', type: 'missing' }] },
          422,
        ),
    })

    await analyze()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Сервер не прийняв запит')
    expect(alert).not.toHaveTextContent('Field required')
  })

  test('an unreachable server is reported', async () => {
    mockApi({ predict: () => Promise.reject(new TypeError('Failed to fetch')) })

    await analyze()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Сервер недоступний')
    expect(alert).toHaveTextContent('Перевірте, що бекенд запущено')
  })

  test('an unexpected status is reported with its code', async () => {
    mockApi({ predict: () => new Response('Internal Server Error', { status: 500 }) })

    await analyze()

    expect(await screen.findByRole('alert')).toHaveTextContent('код 500')
  })

  test('the error disappears after a successful retry', async () => {
    let attempt = 0
    mockApi({
      predict: () =>
        ++attempt === 1
          ? Promise.reject(new TypeError('Failed to fetch'))
          : jsonResponse(makePrediction()),
    })
    const user = await analyze()
    await screen.findByRole('alert')

    await user.click(analyzeButton())

    expect(await screen.findByTestId('predicted-class')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('theme', () => {
  test('switches between light and dark and remembers the choice', async () => {
    mockApi()
    const user = userEvent.setup()
    render(<App />)
    expect(document.documentElement).not.toHaveClass('dark')

    await user.click(screen.getByRole('button', { name: 'Увімкнути темну тему' }))

    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('dark')

    await user.click(screen.getByRole('button', { name: 'Увімкнути світлу тему' }))

    expect(document.documentElement).not.toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('light')
  })

  test('starts with the saved theme', () => {
    mockApi()
    localStorage.setItem('theme', 'dark')

    render(<App />)

    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: 'Увімкнути світлу тему' })).toBeInTheDocument()
  })
})
