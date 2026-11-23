import { ApiError, type ApiErrorCode, type ApiErrorKind } from './errors'
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

interface ErrorBody {
  text: string
  detail?: string
  code?: string
}

async function readBody(response: Response): Promise<ErrorBody> {
  let text = ''
  try {
    text = await response.text()
    const body: unknown = JSON.parse(text)
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      const code = 'code' in body && typeof body.code === 'string' ? body.code : undefined
      return { text, detail: body.detail, code }
    }
  } catch {
    // Not JSON (e.g. a proxy error page or the host refusal): there is no detail to show.
  }
  return { text }
}

/**
 * Codes that decide the kind, whatever the status map says: two 429s, two 403s and a
 * 413 mean different things and the text of the server may change.
 */
const KIND_BY_CODE: Partial<Record<ApiErrorCode, ApiErrorKind>> = {
  login_locked: 'login-blocked',
  rate_limited: 'rate-limited',
  wrong_password: 'wrong-password',
  body_too_large: 'credentials-too-long',
}

function kindOfCode(code: string | undefined): ApiErrorKind | undefined {
  return code === undefined ? undefined : KIND_BY_CODE[code as ApiErrorCode]
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
/** The same answer for an unknown name and a wrong password. A 429 is told apart by its
 * code: the lock after failed attempts (login_locked) or the per-minute limit. */
const LOGIN_KIND_BY_STATUS: KindByStatus = { ...KIND_BY_STATUS, 401: 'login-failed' }
const REGISTER_KIND_BY_STATUS: KindByStatus = { ...KIND_BY_STATUS, 409: 'username-taken' }

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
    const { text, detail, code } = await readBody(response)
    const refusedHost = response.status === 400 && text.trim() === HOST_REFUSAL
    const kind = refusedHost
      ? 'host-not-allowed'
      : (kindOfCode(code) ?? kindByStatus[response.status] ?? 'server')
    const retryAfter = response.status === 429 ? readRetryAfter(response) : undefined
    if (response.status === 401 && session !== null) {
      for (const listener of [...sessionListeners]) {
        listener(session)
      }
    }
    throw new ApiError(kind, { status: response.status, detail, retryAfter, code })
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

/**
 * Delete the account, its sessions and its whole history. A wrong password is a 403 with
 * the code wrong_password; any other 403 comes from the Origin checks.
 */
export async function deleteAccount(password: string): Promise<void> {
  await send('/auth/me', { method: 'DELETE', ...jsonBody({ password }) })
}
