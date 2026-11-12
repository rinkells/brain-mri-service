export type ApiErrorKind =
  | 'invalid-image' // 400
  | 'not-found' // 404
  | 'too-large' // 413
  | 'unsupported-format' // 415
  | 'validation' // 422
  | 'database-unavailable' // 503: history is disabled or its database is down
  | 'unavailable' // no connection, or the dev proxy could not reach the backend
  | 'server' // any other failure

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status?: number
  /** The backend's own explanation, when it sent one as text. */
  readonly detail?: string

  constructor(kind: ApiErrorKind, options: { status?: number; detail?: string } = {}) {
    super(options.detail ?? `API error: ${kind}`)
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status
    this.detail = options.detail
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
