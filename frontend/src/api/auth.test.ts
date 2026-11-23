import { describe, expect, test, vi } from 'vitest'

import {
  bodyTooLarge,
  databaseNotConfigured,
  jsonResponse,
  makeFile,
  makeUser,
  noContent,
  notSignedIn,
  sessionExpired,
  tooMany,
} from '../test/fixtures'
import {
  deleteAccount,
  deletePrediction,
  getHealth,
  getMe,
  getStats,
  listPredictions,
  login,
  logout,
  onSessionExpired,
  predict,
  register,
} from './client'
import { ApiError, describeError } from './errors'

function stubFetch(implementation: () => Response | Promise<Response>) {
  const fetchMock = vi.fn(async () => implementation())
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function callOf(fetchMock: ReturnType<typeof stubFetch>, index = 0): [string, RequestInit] {
  return fetchMock.mock.calls[index] as unknown as [string, RequestInit]
}

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.then(
    () => undefined,
    (reason: unknown) => reason,
  )
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

describe('cookies', () => {
  test('every request sends cookies to the same origin only', async () => {
    const fetchMock = stubFetch(() => jsonResponse(makeUser()))
    const ignore = () => undefined

    await getHealth()
    await getMe()
    await login('alice', 'correct-horse-1').catch(ignore)
    await register('alice', 'correct-horse-1').catch(ignore)
    await logout().catch(ignore)
    await deleteAccount('correct-horse-1').catch(ignore)
    await listPredictions({ limit: 10, offset: 0 }).catch(ignore)
    await getStats().catch(ignore)
    await deletePrediction('x').catch(ignore)
    await predict(makeFile(), { gradcam: false }).catch(ignore)

    expect(fetchMock).toHaveBeenCalledTimes(10)
    for (let index = 0; index < 10; index++) {
      expect(callOf(fetchMock, index)[1].credentials).toBe('same-origin')
    }
  })
})

describe('accounts', () => {
  test('getMe returns the user, or null without a session', async () => {
    stubFetch(() => jsonResponse(makeUser()))
    await expect(getMe()).resolves.toEqual(makeUser())

    stubFetch(() => notSignedIn())
    await expect(getMe()).resolves.toBeNull()
  })

  test.each([
    ['/auth/login', login, 200],
    ['/auth/register', register, 201],
  ] as const)('%s posts the name and password as JSON', async (path, call, status) => {
    const fetchMock = stubFetch(() => jsonResponse(makeUser(), status))

    await expect(call('alice', 'correct-horse-1')).resolves.toEqual(makeUser())

    const [url, init] = callOf(fetchMock)
    expect(url).toBe(path)
    expect(init.method).toBe('POST')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init.body))).toEqual({
      username: 'alice',
      password: 'correct-horse-1',
    })
  })

  test('logout posts and accepts 204', async () => {
    const fetchMock = stubFetch(() => noContent())

    await logout()

    const [url, init] = callOf(fetchMock)
    expect([url, init.method]).toEqual(['/auth/logout', 'POST'])
  })

  test('deleteAccount sends DELETE with the password', async () => {
    const fetchMock = stubFetch(() => noContent())

    await deleteAccount('correct-horse-1')

    const [url, init] = callOf(fetchMock)
    expect([url, init.method]).toEqual(['/auth/me', 'DELETE'])
    expect(JSON.parse(String(init.body))).toEqual({ password: 'correct-horse-1' })
  })

  test('a wrong name or password is login-failed', async () => {
    stubFetch(() => jsonResponse({ detail: "Неправильне ім'я користувача або пароль." }, 401))

    const error = await captureError(login('alice', 'wrong-horse-1'))

    expect(error.kind).toBe('login-failed')
    expect(describeError(error)).toEqual({
      title: 'Не вдалося увійти',
      description: 'Неправильне ім’я користувача або пароль.',
    })
  })

  test('a taken name is username-taken', async () => {
    stubFetch(() => jsonResponse({ detail: "Це ім'я користувача вже зайняте." }, 409))

    const error = await captureError(register('alice', 'correct-horse-1'))

    expect(error.kind).toBe('username-taken')
    expect(describeError(error)).toEqual({
      title: 'Ім’я зайняте',
      description: 'Це ім’я користувача вже зайняте. Оберіть інше.',
    })
  })

  test('a 422 of the registration shows the server text', async () => {
    const detail = 'Пароль не може збігатися з іменем користувача.'
    stubFetch(() => jsonResponse({ detail }, 422))

    const error = await captureError(register('alice_smith', 'alice_smith'))

    expect(error.kind).toBe('validation')
    expect(describeError(error)).toEqual({ title: 'Перевірте дані', description: detail })
  })

  test('a 429 of sign-in is the lock after failed attempts, with the seconds to wait', async () => {
    stubFetch(() =>
      tooMany(660, 'Забагато невдалих спроб входу. Спробуйте ще раз через 660 с.', 'login_locked'),
    )

    const error = await captureError(login('alice', 'correct-horse-1'))

    expect(error.kind).toBe('login-blocked')
    expect(error.retryAfter).toBe(660)
    expect(describeError(error)).toEqual({
      title: 'Вхід тимчасово заблоковано',
      description: 'Забагато невдалих спроб входу. Спробуйте ще раз через 660 с.',
    })
  })

  test('a 429 of registration is the usual rate limit', async () => {
    stubFetch(() => tooMany(42, 'Забагато запитів. Спробуйте ще раз через 42 с.'))

    const error = await captureError(register('alice', 'correct-horse-1'))

    expect(error.kind).toBe('rate-limited')
    expect(describeError(error).description).toBe('Спробуйте ще раз через 42 с.')
  })

  test('a 403 of the account deletion is a wrong password', async () => {
    stubFetch(() => jsonResponse({ detail: 'Неправильний пароль.', code: 'wrong_password' }, 403))

    const error = await captureError(deleteAccount('wrong-horse-1'))

    expect(error.kind).toBe('wrong-password')
    expect(describeError(error).title).toBe('Неправильний пароль')
  })

  test('a 403 of the Origin check during deletion is still about the origin', async () => {
    const detail =
      'Запити з цього джерела (Origin) заборонено. Дозволені джерела задає ALLOWED_ORIGINS.'
    stubFetch(() => jsonResponse({ detail }, 403))

    const error = await captureError(deleteAccount('correct-horse-1'))

    expect(error.kind).toBe('origin-not-allowed')
  })
})

describe('a session that ended (401), handled in one place', () => {
  function listen() {
    const sources: string[] = []
    const stop = onSessionExpired((source) => sources.push(source))
    return { sources, stop }
  }

  test.each([
    ['GET /predictions', () => listPredictions({ limit: 10, offset: 0 })],
    ['GET /stats', () => getStats()],
    ['DELETE /predictions/{id}', () => deletePrediction('x')],
    ['DELETE /auth/me', () => deleteAccount('correct-horse-1')],
  ])('%s with 401 reports the end of the session', async (_, call) => {
    const { sources, stop } = listen()
    stubFetch(() => sessionExpired())

    const error = await captureError(call())

    expect(error.kind).toBe('session-expired')
    expect(describeError(error)).toEqual({
      title: 'Сеанс завершився',
      description: 'Увійдіть знову.',
    })
    expect(sources).toEqual(['other'])
    stop()
  })

  test('/predict with 401 says the result was not saved', async () => {
    const { sources, stop } = listen()
    stubFetch(() => sessionExpired())

    const error = await captureError(predict(makeFile(), { gradcam: false }))

    expect(error.kind).toBe('session-expired-unsaved')
    expect(describeError(error)).toEqual({
      title: 'Сеанс завершився, результат не збережено',
      description: 'Спробуйте ще раз.',
    })
    expect(sources).toEqual(['predict'])
    stop()
  })

  test('a wrong password at sign-in and GET /auth/me without a session are not that', async () => {
    const { sources, stop } = listen()
    stubFetch(() => notSignedIn())

    await getMe()
    await login('alice', 'wrong-horse-1').catch(() => undefined)

    expect(sources).toEqual([])
    stop()
  })

  test('a listener stops hearing once it unsubscribes', async () => {
    const { sources, stop } = listen()
    stop()
    stubFetch(() => sessionExpired())

    await getStats().catch(() => undefined)

    expect(sources).toEqual([])
  })
})

describe('the kind of an error follows its code, not its text', () => {
  test('a 429 of sign-in from the per-minute limit is not the lock after failed attempts', async () => {
    stubFetch(() => tooMany(42, 'Забагато запитів. Спробуйте ще раз через 42 с.', 'rate_limited'))

    const error = await captureError(login('alice', 'correct-horse-1'))

    expect(error.kind).toBe('rate-limited')
    expect(error.code).toBe('rate_limited')
    expect(describeError(error)).toEqual({
      title: 'Забагато запитів',
      description: 'Спробуйте ще раз через 42 с.',
    })
  })

  test('the text alone decides nothing', async () => {
    // The text of the lock with the code of the per-minute limit.
    stubFetch(() =>
      tooMany(10, 'Забагато невдалих спроб входу. Спробуйте ще раз через 10 с.', 'rate_limited'),
    )
    expect((await captureError(login('alice', 'correct-horse-1'))).kind).toBe('rate-limited')

    // "Неправильний пароль." without the code is the Origin check, not a wrong password.
    stubFetch(() => jsonResponse({ detail: 'Неправильний пароль.' }, 403))
    expect((await captureError(deleteAccount('x'))).kind).toBe('origin-not-allowed')

    // The code with another text is still a wrong password.
    stubFetch(() => jsonResponse({ detail: 'Інший текст.', code: 'wrong_password' }, 403))
    expect((await captureError(deleteAccount('x'))).kind).toBe('wrong-password')
  })

  test.each([
    ['login', () => login('alice', 'x'.repeat(9000))],
    ['register', () => register('alice', 'x'.repeat(9000))],
    ['deleteAccount', () => deleteAccount('x'.repeat(9000))],
  ])('a 413 of %s is about the name or password, not about a file', async (_, call) => {
    stubFetch(() => bodyTooLarge())

    const error = await captureError(call())

    expect(error.kind).toBe('credentials-too-long')
    expect(describeError(error)).toEqual({
      title: 'Задовгі дані',
      description: 'Ім’я користувача — не довше за 32 символи, пароль — не довший за 128.',
    })
  })

  test('a history without a database keeps its code', async () => {
    stubFetch(() => databaseNotConfigured())

    const error = await captureError(getStats())

    expect(error.kind).toBe('database-unavailable')
    expect(error.code).toBe('database_not_configured')
  })
})
