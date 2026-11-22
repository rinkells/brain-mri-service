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
  | 'server' // any other failure

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  /** The backend's own explanation, when it sent one as text. */
  readonly detail?: string
  /** Seconds to wait before trying again (Retry-After of a 429), when it was given. */
  readonly retryAfter?: number

  constructor(
    kind: ApiErrorKind,
    options: { status?: number; detail?: string; retryAfter?: number } = {},
  ) {
    super(options.detail ?? `API error: ${kind}`)
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status
    this.detail = options.detail
    this.retryAfter = options.retryAfter
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
      return {
        title: 'Сервер не прийняв запит',
        description: 'Запит сформовано неправильно. Оберіть файл ще раз і повторіть спробу.',
      }
    case 'rate-limited':
      return {
        title: 'Забагато запитів',
        description:
          error.retryAfter === undefined
            ? 'Забагато запитів. Спробуйте ще раз трохи згодом.'
            : `Забагато запитів. Спробуйте ще раз через ${error.retryAfter} с.`,
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
