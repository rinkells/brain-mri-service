import { ApiError, type ApiErrorKind } from './errors'
import type {
  ClassName,
  HealthResponse,
  PredictionPage,
  PredictionResponse,
  StatsResponse,
  User,
} from './types'

type KindByStatus = Partial<Record<number, ApiErrorKind>>

const KIND_BY_STATUS: KindByStatus = {
  400: 'invalid-image',
  // Routes that need a session answer 401 when it has ended or the cookie is not valid.
  401: 'session-expired',
  // The origin checks answer 403 (DELETE /auth/me also for a wrong password, see below).
  403: 'origin-not-allowed',
  404: 'not-found',
  413: 'too-large',
  415: 'unsupported-format',
  422: 'validation',
  429: 'rate-limited',
  // Outside /predict, our backend answers 503 only for the history database.
  503: 'database-unavailable',
  // The Vite dev proxy answers 502 when the backend is not running.
  502: 'unavailable',
  504: 'unavailable',
}

/** Whole seconds from a Retry-After header; undefined when it is missing or not that. */
function readRetryAfter(response: Response): number | undefined {
  const value = response.headers.get('Retry-After')
  return value !== null && /^\d+$/.test(value.trim()) ? Number(value) : undefined
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

/**
 * /predict answers 503 when too many scans are already being analysed, and 401 when the
 * browser sent a session cookie that is no longer valid (the prediction did not run).
 */
const PREDICT_KIND_BY_STATUS: KindByStatus = {
  ...KIND_BY_STATUS,
  401: 'session-expired-unsaved',
  503: 'busy',
}
/** The same answer for an unknown name and a wrong password; 429 after failed attempts. */
const LOGIN_KIND_BY_STATUS: KindByStatus = {
  ...KIND_BY_STATUS,
  401: 'login-failed',
  429: 'login-blocked',
}
const REGISTER_KIND_BY_STATUS: KindByStatus = { ...KIND_BY_STATUS, 409: 'username-taken' }
/** The text DELETE /auth/me sends with its 403 for a wrong password. */
const WRONG_PASSWORD_DETAIL = 'Неправильний пароль.'

// --- A session that ended -----------------------------------------------------------------

/** Where the 401 came from: /predict, or any other route that needs a session. */
export type SessionSource = 'predict' | 'other'
type SessionListener = (source: SessionSource) => void

const sessionListeners = new Set<SessionListener>()

/**
 * Hear about every 401 from a route that needs a session, in one place, whichever part
 * of the interface made the request. Returns the function that stops listening.
 * GET /auth/me without a session and a failed sign-in are not reported.
 */
export function onSessionExpired(listener: SessionListener): () => void {
  sessionListeners.add(listener)
  return () => {
    sessionListeners.delete(listener)
  }
}

interface SendOptions {
  kindByStatus?: KindByStatus
  /** Report a 401 to onSessionExpired listeners as coming from here; null: do not. */
  session?: SessionSource | null
}

async function send(
  url: string,
  init: RequestInit = {},
  { kindByStatus = KIND_BY_STATUS, session = 'other' }: SendOptions = {},
): Promise<Response> {
  let response: Response
  try {
    // The session lives in an HttpOnly cookie, sent to this origin only.
    response = await fetch(url, { ...init, credentials: 'same-origin' })
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
    const retryAfter = response.status === 429 ? readRetryAfter(response) : undefined
    if (response.status === 401 && session !== null) {
      for (const listener of [...sessionListeners]) {
        listener(session)
      }
    }
    throw new ApiError(kind, { status: response.status, detail, retryAfter })
  }
  return response
}

async function requestJson<T>(
  url: string,
  init: RequestInit = {},
  options: SendOptions = {},
): Promise<T> {
  const response = await send(url, init, options)
  return (await response.json()) as T
}

function jsonBody(body: unknown): RequestInit {
  return { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
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
    { kindByStatus: PREDICT_KIND_BY_STATUS, session: 'predict' },
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

// --- Accounts -------------------------------------------------------------------------------

/** The signed-in user, or null when there is no valid session (401). */
export async function getMe(signal?: AbortSignal): Promise<User | null> {
  try {
    return await requestJson<User>('/auth/me', { signal }, { session: null })
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null
    }
    throw error
  }
}

/** Sign in; the response sets the session cookie. */
export function login(username: string, password: string): Promise<User> {
  return requestJson<User>(
    '/auth/login',
    { method: 'POST', ...jsonBody({ username, password }) },
    { kindByStatus: LOGIN_KIND_BY_STATUS, session: null },
  )
}

/** Create an account and sign in at once. */
export function register(username: string, password: string): Promise<User> {
  return requestJson<User>(
    '/auth/register',
    { method: 'POST', ...jsonBody({ username, password }) },
    { kindByStatus: REGISTER_KIND_BY_STATUS, session: null },
  )
}

/** End the session on the server; the response clears the cookie. */
export async function logout(): Promise<void> {
  await send('/auth/logout', { method: 'POST' }, { session: null })
}

/** Delete the account, its sessions and its whole history. */
export async function deleteAccount(password: string): Promise<void> {
  try {
    await send('/auth/me', { method: 'DELETE', ...jsonBody({ password }) })
  } catch (error) {
    if (
      error instanceof ApiError &&
      error.status === 403 &&
      error.detail === WRONG_PASSWORD_DETAIL
    ) {
      throw new ApiError('wrong-password', { status: 403, detail: error.detail })
    }
    throw error
  }
}
