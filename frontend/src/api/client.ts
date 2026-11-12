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
  404: 'not-found',
  413: 'too-large',
  415: 'unsupported-format',
  422: 'validation',
  // Our backend answers 503 only for the history database.
  503: 'database-unavailable',
  // The Vite dev proxy answers 502 when the backend is not running.
  502: 'unavailable',
  504: 'unavailable',
}

async function readDetail(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    // Not JSON (e.g. a proxy error page): there is no detail to show.
  }
  return undefined
}

async function send(url: string, init: RequestInit = {}): Promise<Response> {
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
    throw new ApiError(KIND_BY_STATUS[response.status] ?? 'server', {
      status: response.status,
      detail: await readDetail(response),
    })
  }
  return response
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await send(url, init)
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
  return requestJson<PredictionResponse>(url, { method: 'POST', body, signal: options.signal })
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
