import { describe, expect, test, vi } from 'vitest'

import { jsonResponse, makeFile, makePrediction } from '../test/fixtures'
import { getHealth, predict } from './client'
import { ApiError, describeError } from './errors'

function stubFetch(implementation: () => Response | Promise<Response>) {
  const fetchMock = vi.fn(async () => implementation())
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => undefined,
    (reason: unknown) => reason,
  )
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

describe('predict', () => {
  test('sends the file as multipart form data', async () => {
    const fetchMock = stubFetch(() => jsonResponse(makePrediction()))
    const file = makeFile()

    const result = await predict(file, { gradcam: false })

    expect(result).toEqual(makePrediction())
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/predict')
    expect(init.method).toBe('POST')
    expect((init.body as FormData).get('file')).toBe(file)
  })

  test('asks for the heat map with ?gradcam=true', async () => {
    const fetchMock = stubFetch(() => jsonResponse(makePrediction()))

    await predict(makeFile(), { gradcam: true })

    expect(fetchMock.mock.calls[0]).toContain('/predict?gradcam=true')
  })

  test.each([
    [400, 'invalid-image'],
    [413, 'too-large'],
    [415, 'unsupported-format'],
  ] as const)('maps %i to %s and keeps the server detail', async (status, kind) => {
    stubFetch(() => jsonResponse({ detail: 'Пояснення сервера.' }, status))

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe(kind)
    expect(error.status).toBe(status)
    expect(error.detail).toBe('Пояснення сервера.')
  })

  test('maps 422 to a validation error without a text detail', async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: [{ loc: ['body', 'file'], msg: 'Field required', type: 'missing' }] },
        422,
      ),
    )

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe('validation')
    expect(error.detail).toBeUndefined()
  })

  test('treats a network failure as an unavailable server', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe('unavailable')
  })

  test('treats a 502 from the dev proxy as an unavailable server', async () => {
    stubFetch(() => new Response('', { status: 502 }))

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe('unavailable')
    expect(error.detail).toBeUndefined()
  })

  test('reports other statuses as server errors', async () => {
    stubFetch(() => new Response('Internal Server Error', { status: 500 }))

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe('server')
    expect(error.status).toBe(500)
  })

  test('passes an abort through instead of reporting the server as down', async () => {
    const controller = new AbortController()
    const abortError = new DOMException('Aborted', 'AbortError')
    stubFetch(() => {
      controller.abort()
      return Promise.reject(abortError)
    })

    await expect(predict(makeFile(), { gradcam: false, signal: controller.signal })).rejects.toBe(
      abortError,
    )
  })
})

test('getHealth returns the health payload', async () => {
  stubFetch(() => jsonResponse({ status: 'ok', device: 'cpu', classes: [] }))

  await expect(getHealth()).resolves.toEqual({ status: 'ok', device: 'cpu', classes: [] })
})

describe('describeError', () => {
  test('prefers the server detail', () => {
    expect(
      describeError(new ApiError('too-large', { status: 413, detail: 'Не більше 10 МБ.' })),
    ).toEqual({
      title: 'Файл завеликий',
      description: 'Не більше 10 МБ.',
    })
  })

  test('has a fallback text for every kind', () => {
    const kinds = [
      'invalid-image',
      'too-large',
      'unsupported-format',
      'validation',
      'unavailable',
      'server',
    ] as const
    for (const kind of kinds) {
      const { title, description } = describeError(new ApiError(kind, { status: 500 }))
      expect(title).not.toBe('')
      expect(description).not.toBe('')
    }
  })

  test('describes unknown errors', () => {
    expect(describeError(new Error('boom')).title).toBe('Щось пішло не так')
  })
})
