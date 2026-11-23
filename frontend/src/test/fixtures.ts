import { vi } from 'vitest'

import type {
  ClassName,
  PredictionPage,
  PredictionRecord,
  PredictionResponse,
  StatsResponse,
  User,
} from '../api/types'

export function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status })
}

export function healthyResponse(): Response {
  return jsonResponse({
    status: 'ok',
    device: 'cpu',
    classes: ['glioma', 'meningioma', 'notumor', 'pituitary'],
    model_version: 'resnet18_v2c',
  })
}

export function makePrediction(overrides: Partial<PredictionResponse> = {}): PredictionResponse {
  return {
    id: '6f1c2a3e-0000-4000-8000-000000000001',
    predicted_class: 'meningioma',
    probabilities: { glioma: 0.05, meningioma: 0.8, notumor: 0.12, pituitary: 0.03 },
    image_size: { width: 512, height: 512 },
    warnings: [],
    gradcam_png_base64: null,
    model_version: 'resnet18_v2c',
    ...overrides,
  }
}

/** A saved prediction; `n` makes the id and time unique (higher n = newer). */
export function makeRecord(n: number, overrides: Partial<PredictionRecord> = {}): PredictionRecord {
  const predicted: ClassName = overrides.predicted_class ?? 'glioma'
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    created_at: new Date(Date.UTC(2026, 10, 12, 9, 0, n)).toISOString(),
    predicted_class: predicted,
    probabilities: { glioma: 0.1, meningioma: 0.1, notumor: 0.1, pituitary: 0.1, [predicted]: 0.7 },
    image_size: { width: 512, height: 512 },
    warnings: [],
    gradcam_requested: false,
    latency_ms: 100,
    model_version: 'resnet18_v2c',
    ...overrides,
  }
}

export function makePage(
  items: PredictionRecord[],
  overrides: Partial<PredictionPage> = {},
): PredictionPage {
  return { items, total: items.length, limit: 10, offset: 0, ...overrides }
}

export function makeStats(overrides: Partial<StatsResponse> = {}): StatsResponse {
  return {
    total: 4,
    by_class: { glioma: 2, meningioma: 1, notumor: 1, pituitary: 0 },
    with_warnings_share: 0.25,
    average_latency_ms: 154.5,
    warnings_by_code: { unexpected_image_size: 1, low_confidence: 0, not_mri_like: 0 },
    ...overrides,
  }
}

/** A file with the given name, MIME type and (reported) size in bytes. */
export function makeFile(name = 'scan.png', type = 'image/png', size = 2048): File {
  const file = new File(['x'], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: '5d7a3c1e-0000-4000-8000-0000000000a1',
    username: 'alice',
    created_at: '2026-11-23T10:00:00Z',
    ...overrides,
  }
}

/** What GET /auth/me answers without a session. */
export function notSignedIn(): Response {
  return jsonResponse({ detail: 'Потрібно увійти в обліковий запис.' }, 401)
}

/** What the API answers when the session cookie names no valid session. */
export function sessionExpired(): Response {
  return jsonResponse({ detail: 'Сесія недійсна або закінчилася. Увійдіть знову.' }, 401)
}

export function noContent(): Response {
  return new Response(null, { status: 204 })
}

/** A 429 with the Retry-After header and the server's text. */
export function tooMany(seconds: number, detail: string): Response {
  return Response.json({ detail }, { status: 429, headers: { 'Retry-After': String(seconds) } })
}

/** The JSON body a request was sent with. */
export function bodyOf(init: RequestInit | undefined): unknown {
  return JSON.parse(String(init?.body))
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>

interface Handlers {
  health?: Handler
  predict?: Handler
  /** GET /predictions */
  predictions?: Handler
  /** DELETE /predictions/{id} */
  deletePrediction?: Handler
  stats?: Handler
  /** GET /auth/me; defaults to 401 (nobody signed in). */
  me?: Handler
  login?: Handler
  register?: Handler
  /** POST /auth/logout; defaults to 204. */
  logout?: Handler
  /** DELETE /auth/me */
  deleteAccount?: Handler
}

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), 'http://localhost').pathname
}

/**
 * Replace fetch with a router for the API paths. `health` defaults to a healthy
 * backend, `me` to an anonymous visitor and `logout` to 204; every other route must be
 * given when the test uses it.
 */
export function mockApi(handlers: Handlers = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const path = pathOf(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    let handler: Handler | undefined
    if (path === '/health') {
      handler = handlers.health ?? (() => healthyResponse())
    } else if (path === '/predict') {
      handler = handlers.predict
    } else if (path === '/stats') {
      handler = handlers.stats
    } else if (path.startsWith('/predictions')) {
      handler = method === 'DELETE' ? handlers.deletePrediction : handlers.predictions
    } else if (path === '/auth/me') {
      handler = method === 'DELETE' ? handlers.deleteAccount : (handlers.me ?? notSignedIn)
    } else if (path === '/auth/login') {
      handler = handlers.login
    } else if (path === '/auth/register') {
      handler = handlers.register
    } else if (path === '/auth/logout') {
      handler = handlers.logout ?? noContent
    }

    if (!handler) {
      throw new Error(`Unexpected request: ${method} ${url}`)
    }
    return handler(url, init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

type FetchMock = ReturnType<typeof mockApi>

/** Calls made to the given path (and method), as [url, init] pairs. */
export function callsTo(fetchMock: FetchMock, path: string, method = 'GET') {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      pathOf(input).startsWith(path) && (init?.method ?? 'GET').toUpperCase() === method,
  ) as [string, RequestInit | undefined][]
}

/** Calls made to POST /predict, as [url, init] pairs. */
export function predictCalls(fetchMock: FetchMock) {
  return fetchMock.mock.calls.filter(([input]) => pathOf(input) === '/predict') as [
    string,
    RequestInit,
  ][]
}

/** Query parameters of a request URL. */
export function queryOf(url: string): Record<string, string> {
  return Object.fromEntries(new URL(url, 'http://localhost').searchParams)
}
