export type ApiErrorKind =
  | 'invalid-image' // 400
  | 'host-not-allowed' // 400 "Invalid host header": the address is not in ALLOWED_HOSTS
  | 'origin-not-allowed' // 403: this page's origin is not in ALLOWED_ORIGINS
  | 'not-found' // 404
  | 'too-large' // 413
  | 'unsupported-format' // 415
  | 'validation' // 422
  | 'rate-limited' // 429: too many requests from this address in the last minute
  | 'busy' // 503 from /predict: too many scans are being analysed
  | 'database-unavailable' // 503 from the history: it is disabled or its database is down
  | 'unavailable' // no connection, or the dev proxy could not reach the backend
  | 'login-failed' // 401 from /auth/login: wrong name or password (one answer for both)
  | 'login-blocked' // 429 from /auth/login: too many failed attempts for this name
  | 'username-taken' // 409 from /auth/register
  | 'wrong-password' // 403 from DELETE /auth/me
  | 'session-expired' // 401 from a route that needs a session: it ended or is invalid
  | 'session-expired-unsaved' // 401 from /predict: the session ended, nothing was saved
  | 'credentials-too-long' // 413 body_too_large from /auth/*: the name or password is too long
  | 'server' // any other failure

/**
 * Stable codes the backend adds to some error bodies (ErrorResponse.code). The interface
 * tells these cases apart by the code; the text of `detail` is only shown.
 */
export type ApiErrorCode =
  | 'database_not_configured' // 503: no DATABASE_URL, so no accounts and no history
  | 'login_locked' // 429: too many wrong passwords for this name from this address
  | 'rate_limited' // 429: too many requests from this address in the last minute
  | 'wrong_password' // 403 from DELETE /auth/me
  | 'body_too_large' // 413 from /auth/*

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  /** The backend's own explanation, when it sent one as text. */
  readonly detail?: string
  /** Seconds to wait before trying again (Retry-After of a 429), when it was given. */
  readonly retryAfter?: number
  /** The stable code of the error body, when the backend sent one (any string). */
  readonly code?: string

  constructor(
    kind: ApiErrorKind,
    options: { status?: number; detail?: string; retryAfter?: number; code?: string } = {},
  ) {
    super(options.detail ?? `API error: ${kind}`)
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status
    this.detail = options.detail
    this.retryAfter = options.retryAfter
    this.code = options.code
  }
}

export interface ErrorDescription {
  title: string
  description: string
}

/** Turn any error from the API client into text for the user. */
export function describeError(error: unknown): ErrorDescription {
  if (!(error instanceof ApiError)) {
    return {
      title: 'Щось пішло не так',
      description: 'Сталася неочікувана помилка. Спробуйте ще раз.',
    }
  }

  switch (error.kind) {
    case 'invalid-image':
      return {
        title: 'Файл не вдалося прочитати',
        description:
          error.detail ?? 'Сервер не розпізнав файл як зображення. Можливо, файл пошкоджений.',
      }
    case 'host-not-allowed':
      return {
        title: 'Адресу сервера не дозволено',
        description:
          'Сервер не відповідає на адресу, за якою відкрито інтерфейс. Відкрийте його через ' +
          'localhost або додайте цю адресу до ALLOWED_HOSTS у налаштуваннях сервера.',
      }
    case 'origin-not-allowed':
      return {
        title: 'Цій сторінці заборонено надсилати запити',
        description:
          'Сервер приймає аналіз і видалення лише зі сторінок, перелічених в ALLOWED_ORIGINS. ' +
          'Відкрийте інтерфейс за дозволеною адресою (типово http://localhost:5173) або ' +
          'додайте адресу цієї сторінки до ALLOWED_ORIGINS у налаштуваннях сервера.',
      }
    case 'not-found':
      return {
        title: 'Запис не знайдено',
        description: error.detail ?? 'Можливо, його вже видалили.',
      }
    case 'database-unavailable':
      return {
        title: 'Історія недоступна',
        description: error.detail ?? 'База даних недоступна. Спробуйте пізніше.',
      }
    case 'too-large':
      return {
        title: 'Файл завеликий',
        description: error.detail ?? 'Сервер не приймає такі великі файли.',
      }
    case 'unsupported-format':
      return {
        title: 'Непідтримуваний формат',
        description: error.detail ?? 'Сервер приймає лише знімки у форматі JPEG або PNG.',
      }
    case 'validation':
      // /auth/* explain the problem in one sentence; FastAPI's own list is not shown.
      return error.detail
        ? { title: 'Перевірте дані', description: error.detail }
        : {
            title: 'Сервер не прийняв запит',
            description: 'Запит сформовано неправильно. Оберіть файл ще раз і повторіть спробу.',
          }
    case 'login-failed':
      return {
        title: 'Не вдалося увійти',
        description: 'Неправильне ім’я користувача або пароль.',
      }
    case 'login-blocked':
      return {
        title: 'Вхід тимчасово заблоковано',
        description:
          'Забагато невдалих спроб входу. ' +
          (error.retryAfter === undefined
            ? 'Спробуйте ще раз трохи згодом.'
            : `Спробуйте ще раз через ${error.retryAfter} с.`),
      }
    case 'username-taken':
      return {
        title: 'Ім’я зайняте',
        description: 'Це ім’я користувача вже зайняте. Оберіть інше.',
      }
    case 'wrong-password':
      return {
        title: 'Неправильний пароль',
        description: 'Обліковий запис не видалено. Перевірте пароль і спробуйте ще раз.',
      }
    case 'credentials-too-long':
      return {
        title: 'Задовгі дані',
        description: 'Ім’я користувача — не довше за 32 символи, пароль — не довший за 128.',
      }
    case 'session-expired':
      return { title: 'Сеанс завершився', description: 'Увійдіть знову.' }
    case 'session-expired-unsaved':
      return {
        title: 'Сеанс завершився, результат не збережено',
        description: 'Спробуйте ще раз.',
      }
    case 'rate-limited':
      return {
        title: 'Забагато запитів',
        description:
          error.retryAfter === undefined
            ? 'Спробуйте ще раз трохи згодом.'
            : `Спробуйте ще раз через ${error.retryAfter} с.`,
      }
    case 'busy':
      return {
        title: 'Сервер зайнятий',
        description:
          error.detail ?? 'Зараз аналізується забагато знімків. Спробуйте за кілька секунд.',
      }
    case 'unavailable':
      return {
        title: 'Сервер недоступний',
        description:
          'Не вдалося з’єднатися із сервером аналізу. Перевірте, що бекенд запущено, і спробуйте ще раз.',
      }
    case 'server':
      return {
        title: 'Помилка сервера',
        description: `Сервер не зміг обробити запит (код ${error.status ?? 'невідомий'}). Спробуйте ще раз пізніше.`,
      }
  }
}
