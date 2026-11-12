import { ApiError, type ApiErrorKind } from './errors'
import type { HealthResponse, PredictionResponse } from './types'

const KIND_BY_STATUS: Partial<Record<number, ApiErrorKind>> = {
  400: 'invalid-image',
  413: 'too-large',
  415: 'unsupported-format',
  422: 'validation',
  // The Vite dev proxy answers 502 when the backend is not running.
  502: 'unavailable',
  503: 'unavailable',
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

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
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
  return (await response.json()) as T
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { signal })
}

export function predict(
  file: File,
  options: { gradcam: boolean; signal?: AbortSignal },
): Promise<PredictionResponse> {
  const body = new FormData()
  body.append('file', file)
  const url = options.gradcam ? '/predict?gradcam=true' : '/predict'
  return request<PredictionResponse>(url, { method: 'POST', body, signal: options.signal })
}
