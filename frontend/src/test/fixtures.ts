import { vi } from 'vitest'

import type { PredictionResponse } from '../api/types'

export function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status })
}

export function healthyResponse(): Response {
  return jsonResponse({
    status: 'ok',
    device: 'cpu',
    classes: ['glioma', 'meningioma', 'notumor', 'pituitary'],
  })
}

export function makePrediction(overrides: Partial<PredictionResponse> = {}): PredictionResponse {
  return {
    predicted_class: 'meningioma',
    probabilities: { glioma: 0.05, meningioma: 0.8, notumor: 0.12, pituitary: 0.03 },
    image_size: { width: 512, height: 512 },
    warnings: [],
    gradcam_png_base64: null,
    ...overrides,
  }
}

/** A file with the given name, MIME type and (reported) size in bytes. */
export function makeFile(name = 'scan.png', type = 'image/png', size = 2048): File {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

/**
 * Replace fetch with a router for the two API paths. `health` defaults to a
 * healthy backend; `predict` must be given when the test submits a scan.
 */
export function mockApi(handlers: { health?: Handler; predict?: Handler } = {}) {
  const health = handlers.health ?? (() => healthyResponse())
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/health')) {
      return health(url, init)
    }
    if (url.startsWith('/predict') && handlers.predict) {
      return handlers.predict(url, init)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Calls made to /predict, as [url, init] pairs. */
export function predictCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls.filter(([input]) => String(input).startsWith('/predict')) as [
    string,
    RequestInit,
  ][]
}
