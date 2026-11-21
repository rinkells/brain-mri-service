import { ApiError, type ApiErrorKind } from './errors'
import type {
  ClassName,
  HealthResponse,
  PredictionPage,
  PredictionResponse,
  StatsResponse,
} from './types'

const KIND_BY_STATUS: Partial<Record<number, ApiErrorKind>> = {
  400: 'invalid-image',
  // Only the origin check answers 403.
  403: 'origin-not-allowed',
  404: 'not-found',
  413: 'too-large',
  415: 'unsupported-format',
  422: 'validation',
  // Outside /predict, our backend answers 503 only for the history database.
  503: 'database-unavailable',
  // The Vite dev proxy answers 502 when the backend is not running.
  502: 'unavailable',
  504: 'unavailable',
}

// Starlette's TrustedHostMiddleware answers a Host outside ALLOWED_HOSTS with this
// plain text and status 400, the status /predict uses for a broken image.
const HOST_REFUSAL = 'Invalid host header'

async function readBody(response: Response): Promise<{ text: string; detail?: string }> {
  let text = ''
  try {
    text = await response.text()
    const body: unknown = JSON.parse(text)
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return { text, detail: body.detail }
    }
  } catch {
    // Not JSON (e.g. a proxy error page or the host refusal): there is no detail to show.
  }
  return { text }
}

/** /predict answers 503 when too many scans are already being analysed. */
const PREDICT_KIND_BY_STATUS: Partial<Record<number, ApiErrorKind>> = {
  ...KIND_BY_STATUS,
  503: 'busy',
}

async function send(
  url: string,
  init: RequestInit = {},
  kindByStatus = KIND_BY_STATUS,
): Promise<Response> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch (error) {
    if (init.signal?.aborted) {
      throw error
    }
    throw new ApiError('unavailable')
  }

  if (!response.ok) {
    const { text, detail } = await readBody(response)
    const refusedHost = response.status === 400 && text.trim() === HOST_REFUSAL
    const kind = refusedHost ? 'host-not-allowed' : (kindByStatus[response.status] ?? 'server')
    throw new ApiError(kind, { status: response.status, detail })
  }
  return response
}

async function requestJson<T>(
  url: string,
  init: RequestInit = {},
  kindByStatus = KIND_BY_STATUS,
): Promise<T> {
  const response = await send(url, init, kindByStatus)
  return (await response.json()) as T
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return requestJson<HealthResponse>('/health', { signal })
}

export function predict(
  file: File,
  options: { gradcam: boolean; signal?: AbortSignal },
): Promise<PredictionResponse> {
  const body = new FormData()
  body.append('file', file)
  const url = options.gradcam ? '/predict?gradcam=true' : '/predict'
  return requestJson<PredictionResponse>(
    url,
    { method: 'POST', body, signal: options.signal },
    PREDICT_KIND_BY_STATUS,
  )
}

export interface HistoryQuery {
  limit: number
  offset: number
  predictedClass?: ClassName | null
}

export function listPredictions(
  query: HistoryQuery,
  signal?: AbortSignal,
): Promise<PredictionPage> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) })
  if (query.predictedClass) {
    params.set('predicted_class', query.predictedClass)
  }
  return requestJson<PredictionPage>(`/predictions?${params}`, { signal })
}

export async function deletePrediction(id: string, signal?: AbortSignal): Promise<void> {
  await send(`/predictions/${encodeURIComponent(id)}`, { method: 'DELETE', signal })
}

export function getStats(signal?: AbortSignal): Promise<StatsResponse> {
  return requestJson<StatsResponse>('/stats', { signal })
}
