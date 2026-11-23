import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import App from './App'
import {
  bodyOf,
  bodyTooLarge,
  callsTo,
  databaseNotConfigured,
  jsonResponse,
  makeFile,
  makePage,
  makePrediction,
  makeRecord,
  makeStats,
  makeUser,
  mockApi,
  noContent,
  sessionExpired,
  tooMany,
} from './test/fixtures'

const PASSWORD = 'correct-horse-1'

function signedIn(user = makeUser()) {
  return () => jsonResponse(user)
}

function banner() {
  return screen.getByRole('banner')
}

function form(name: 'Вхід' | 'Реєстрація') {
  return screen.getByRole('form', { name })
}

function field(label: string) {
  return screen.getByLabelText(label) as HTMLInputElement
}

async function openAccountMenu(user: UserEvent, name = 'alice') {
  await user.click(await within(banner()).findByRole('button', { name: new RegExp(name) }))
}

function historyHandlers() {
  return {
    predictions: () => jsonResponse(makePage([makeRecord(1)])),
    stats: () => jsonResponse(makeStats()),
  }
}

async function analyze(user: UserEvent) {
  await user.upload(screen.getByLabelText('Файл знімка МРТ'), makeFile())
  await user.click(screen.getByRole('button', { name: 'Проаналізувати' }))
}

// --- Who is signed in ---------------------------------------------------------------

describe('the header', () => {
  test('an anonymous visitor gets links to sign in and to register', async () => {
    const fetchMock = mockApi()

    render(<App />)

    const signIn = await within(banner()).findByRole('link', { name: 'Увійти' })
    expect(signIn).toHaveAttribute('href', '#/login')
    expect(within(banner()).getByRole('link', { name: 'Зареєструватися' })).toHaveAttribute(
      'href',
      '#/register',
    )
    expect(callsTo(fetchMock, '/auth/me')).toHaveLength(1)
  })

  test('a signed-in user sees the name, with a menu to sign out or delete the account', async () => {
    mockApi({ me: signedIn() })
    const user = userEvent.setup()
    render(<App />)

    const menu = await within(banner()).findByRole('button', { name: /alice/ })
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(within(banner()).queryByRole('link', { name: 'Увійти' })).not.toBeInTheDocument()

    await user.click(menu)

    expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Вийти' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Видалити акаунт' })).toBeVisible()

    await user.keyboard('{Escape}')

    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Вийти' })).not.toBeInTheDocument()
    expect(menu).toHaveFocus()
  })
})

// --- Sign-in --------------------------------------------------------------------------

describe('the sign-in page', () => {
  test('has labelled fields with the right autocomplete and starts in the name field', async () => {
    mockApi()
    window.location.hash = '#/login'

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Вхід' })).toBeInTheDocument()
    const username = field('Ім’я користувача')
    const password = field('Пароль')
    expect(username).toHaveAttribute('autocomplete', 'username')
    expect(password).toHaveAttribute('autocomplete', 'current-password')
    expect(password).toHaveAttribute('type', 'password')
    expect(username).toHaveFocus()
    expect(within(form('Вхід')).getByRole('link', { name: 'Зареєструватися' })).toHaveAttribute(
      'href',
      '#/register',
    )
  })

  test('the switch shows and hides the password', async () => {
    mockApi()
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    const toggle = await screen.findByRole('checkbox', { name: 'Показати пароль' })
    await user.click(toggle)
    expect(field('Пароль')).toHaveAttribute('type', 'text')
    await user.click(toggle)
    expect(field('Пароль')).toHaveAttribute('type', 'password')
  })

  test('empty fields are refused without asking the server', async () => {
    const fetchMock = mockApi()
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    const signIn = await screen.findByRole('form', { name: 'Вхід' })
    await user.click(within(signIn).getByRole('button', { name: 'Увійти' }))

    expect(field('Ім’я користувача')).toHaveAttribute('aria-invalid', 'true')
    expect(field('Ім’я користувача')).toHaveAccessibleDescription('Введіть ім’я користувача.')
    expect(field('Пароль')).toHaveAccessibleDescription('Введіть пароль.')
    expect(field('Ім’я користувача')).toHaveFocus()
    expect(callsTo(fetchMock, '/auth/login', 'POST')).toHaveLength(0)
  })

  test('signing in shows the name and returns to the page the visitor came from', async () => {
    const fetchMock = mockApi({ login: signedIn(), ...historyHandlers() })
    window.location.hash = '#/history'
    const user = userEvent.setup()
    render(<App />)

    await user.click(await within(screen.getByRole('main')).findByRole('link', { name: 'Увійти' }))
    await user.type(field('Ім’я користувача'), 'alice')
    await user.type(field('Пароль'), PASSWORD)
    await user.click(within(form('Вхід')).getByRole('button', { name: 'Увійти' }))

    expect(await within(banner()).findByRole('button', { name: /alice/ })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/history')
    expect(await screen.findByRole('heading', { name: 'Історія прогнозів' })).toBeInTheDocument()
    const [[, init]] = callsTo(fetchMock, '/auth/login', 'POST')
    expect(bodyOf(init)).toEqual({ username: 'alice', password: PASSWORD })
  })

  test('a wrong name or password is explained, and the password is cleared', async () => {
    mockApi({
      login: () => jsonResponse({ detail: "Неправильне ім'я користувача або пароль." }, 401),
    })
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    await user.type(await screen.findByLabelText('Ім’я користувача'), 'alice')
    await user.type(field('Пароль'), 'wrong-horse-22')
    await user.click(within(form('Вхід')).getByRole('button', { name: 'Увійти' }))

    const alert = await within(form('Вхід')).findByRole('alert')
    expect(alert).toHaveTextContent('Не вдалося увійти')
    expect(alert).toHaveTextContent('Неправильне ім’я користувача або пароль.')
    expect(field('Пароль')).toHaveValue('')
    expect(field('Ім’я користувача')).toHaveValue('alice')
    expect(within(banner()).getByRole('link', { name: 'Увійти' })).toBeInTheDocument()
  })

  test('after too many failed attempts it says how long to wait', async () => {
    mockApi({
      login: () =>
        tooMany(
          660,
          'Забагато невдалих спроб входу. Спробуйте ще раз через 660 с.',
          'login_locked',
        ),
    })
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    await user.type(await screen.findByLabelText('Ім’я користувача'), 'alice')
    await user.type(field('Пароль'), PASSWORD)
    await user.click(within(form('Вхід')).getByRole('button', { name: 'Увійти' }))

    const alert = await within(form('Вхід')).findByRole('alert')
    expect(alert).toHaveTextContent('Вхід тимчасово заблоковано')
    expect(alert).toHaveTextContent('Спробуйте ще раз через 660 с.')
  })
})

// --- Registration ------------------------------------------------------------------------

describe('the registration page', () => {
  async function openRegistration() {
    window.location.hash = '#/register'
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Реєстрація' })
    return user
  }

  async function fill(user: UserEvent, username: string, password: string, confirm = password) {
    if (username) await user.type(field('Ім’я користувача'), username)
    if (password) await user.type(field('Пароль'), password)
    if (confirm) await user.type(field('Підтвердження пароля'), confirm)
    await user.click(within(form('Реєстрація')).getByRole('button', { name: 'Зареєструватися' }))
  }

  test('both password fields ask for a new password', async () => {
    mockApi()
    await openRegistration()

    expect(field('Ім’я користувача')).toHaveAttribute('autocomplete', 'username')
    expect(field('Пароль')).toHaveAttribute('autocomplete', 'new-password')
    expect(field('Підтвердження пароля')).toHaveAttribute('autocomplete', 'new-password')
  })

  test('the rules of the backend are checked before sending', async () => {
    const fetchMock = mockApi()
    const user = await openRegistration()

    await fill(user, 'ab', 'short', 'other')

    expect(field('Ім’я користувача')).toHaveAccessibleDescription(
      'Ім’я користувача має містити від 3 до 32 символів: латинські літери, цифри, «_», «.» або «-».',
    )
    expect(field('Пароль')).toHaveAccessibleDescription(
      'Пароль має містити від 10 до 128 символів.',
    )
    expect(field('Підтвердження пароля')).toHaveAccessibleDescription('Паролі не збігаються.')
    expect(field('Ім’я користувача')).toHaveFocus()
    expect(callsTo(fetchMock, '/auth/register', 'POST')).toHaveLength(0)
  })

  test('a password equal to the name is refused', async () => {
    mockApi()
    const user = await openRegistration()

    await fill(user, 'Alice_Smith', 'aLICE_sMITH')

    expect(field('Пароль')).toHaveAttribute('aria-invalid', 'true')
    expect(field('Пароль')).toHaveAccessibleDescription(
      'Пароль не може збігатися з іменем користувача.',
    )
    expect(field('Пароль')).toHaveFocus()
  })

  test('a taken name is explained, and both passwords are cleared', async () => {
    mockApi({ register: () => jsonResponse({ detail: "Це ім'я користувача вже зайняте." }, 409) })
    const user = await openRegistration()

    await fill(user, 'alice', PASSWORD)

    const alert = await within(form('Реєстрація')).findByRole('alert')
    expect(alert).toHaveTextContent('Це ім’я користувача вже зайняте. Оберіть інше.')
    expect(field('Пароль')).toHaveValue('')
    expect(field('Підтвердження пароля')).toHaveValue('')
    expect(field('Ім’я користувача')).toHaveValue('alice')
  })

  test('a 422 shows the explanation of the server', async () => {
    const detail = 'Пароль не може збігатися з іменем користувача.'
    mockApi({ register: () => jsonResponse({ detail }, 422) })
    const user = await openRegistration()

    await fill(user, 'alice', PASSWORD)

    const alert = await within(form('Реєстрація')).findByRole('alert')
    expect(alert).toHaveTextContent('Перевірте дані')
    expect(alert).toHaveTextContent(detail)
  })

  test('too many requests say how long to wait', async () => {
    mockApi({ register: () => tooMany(42, 'Забагато запитів. Спробуйте ще раз через 42 с.') })
    const user = await openRegistration()

    await fill(user, 'alice', PASSWORD)

    expect(await within(form('Реєстрація')).findByRole('alert')).toHaveTextContent(
      'Спробуйте ще раз через 42 с.',
    )
  })

  test('registering signs in and goes to the main page', async () => {
    const fetchMock = mockApi({ register: () => jsonResponse(makeUser(), 201) })
    const user = await openRegistration()

    await fill(user, 'alice', PASSWORD)

    expect(await within(banner()).findByRole('button', { name: /alice/ })).toBeInTheDocument()
    expect(window.location.hash).toBe('#/')
    expect(screen.queryByRole('form', { name: 'Реєстрація' })).not.toBeInTheDocument()
    const [[, init]] = callsTo(fetchMock, '/auth/register', 'POST')
    expect(bodyOf(init)).toEqual({ username: 'alice', password: PASSWORD })
  })
})

// --- Anonymous use ---------------------------------------------------------------------

describe('an anonymous visitor', () => {
  test('can analyse, and the result says it was not saved, with a link to sign in', async () => {
    mockApi({ predict: () => jsonResponse(makePrediction({ id: null })) })
    const user = userEvent.setup()
    render(<App />)
    await within(banner()).findByRole('link', { name: 'Увійти' })

    await analyze(user)

    const link = await screen.findByRole('link', { name: 'Увійдіть' })
    expect(link).toHaveAttribute('href', '#/login')
    expect(link.closest('p')).toHaveTextContent(
      'Результат не збережено. Увійдіть, щоб зберігати історію.',
    )
    expect(screen.getByTestId('predicted-class')).toHaveTextContent('Менінгіома')
  })

  test('sees an explanation with sign-in and registration on the history page', async () => {
    const fetchMock = mockApi()
    window.location.hash = '#/history'

    render(<App />)

    const main = screen.getByRole('main')
    expect(
      await within(main).findByRole('heading', { name: 'Історія доступна після входу' }),
    ).toBeInTheDocument()
    expect(within(main).getByRole('link', { name: 'Увійти' })).toHaveAttribute('href', '#/login')
    expect(within(main).getByRole('link', { name: 'Зареєструватися' })).toHaveAttribute(
      'href',
      '#/register',
    )
    expect(screen.queryByText('Помилка сервера')).not.toBeInTheDocument()
    expect(callsTo(fetchMock, '/predictions')).toHaveLength(0)
    expect(callsTo(fetchMock, '/stats')).toHaveLength(0)
  })
})

describe('a signed-in user', () => {
  test('does not get the note about saving', async () => {
    mockApi({ me: signedIn(), predict: () => jsonResponse(makePrediction()) })
    const user = userEvent.setup()
    render(<App />)
    await within(banner()).findByRole('button', { name: /alice/ })

    await analyze(user)

    expect(await screen.findByText(/Результат збережено в історії/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Увійдіть' })).not.toBeInTheDocument()
  })
})

// --- A session that ended --------------------------------------------------------------

describe('a session that ended', () => {
  test('a 401 from the history signs the user out and says so', async () => {
    mockApi({ me: signedIn(), predictions: sessionExpired, stats: sessionExpired })
    window.location.hash = '#/history'

    render(<App />)

    // Only the notice: the history, with its own error boxes, is gone.
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1))
    const notice = screen.getByRole('alert')
    expect(notice).toHaveTextContent('Сеанс завершився')
    expect(notice).toHaveTextContent('Увійдіть знову.')
    expect(await within(banner()).findByRole('link', { name: 'Увійти' })).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Історія доступна після входу' }),
    ).toBeInTheDocument()
    expect(screen.queryByText('Помилка сервера')).not.toBeInTheDocument()
  })

  test('a 401 from /predict says the result was not saved; the next try is anonymous', async () => {
    let calls = 0
    mockApi({
      me: signedIn(),
      predict: () =>
        ++calls === 1 ? sessionExpired() : jsonResponse(makePrediction({ id: null })),
    })
    const user = userEvent.setup()
    render(<App />)
    await within(banner()).findByRole('button', { name: /alice/ })

    await analyze(user)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Сеанс завершився, результат не збережено')
    expect(alert).toHaveTextContent('Спробуйте ще раз.')
    expect(await within(banner()).findByRole('link', { name: 'Увійти' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Проаналізувати' }))

    expect(await screen.findByRole('link', { name: 'Увійдіть' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

// --- Sign-out and deleting the account ---------------------------------------------------

describe('signing out', () => {
  test('posts /auth/logout, goes to the main page and forgets the history', async () => {
    const fetchMock = mockApi({ me: signedIn(), ...historyHandlers() })
    window.location.hash = '#/history'
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: 'Історія прогнозів' })
    expect(await screen.findAllByRole('row')).not.toHaveLength(0)

    await openAccountMenu(user)
    await user.click(screen.getByRole('button', { name: 'Вийти' }))

    expect(await within(banner()).findByRole('link', { name: 'Увійти' })).toBeInTheDocument()
    expect(callsTo(fetchMock, '/auth/logout', 'POST')).toHaveLength(1)
    expect(window.location.hash).toBe('#/')
    expect(screen.queryByRole('heading', { name: 'Історія прогнозів' })).not.toBeInTheDocument()

    window.location.hash = '#/history'
    expect(
      await screen.findByRole('heading', { name: 'Історія доступна після входу' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('row')).not.toBeInTheDocument()
    expect(callsTo(fetchMock, '/predictions')).toHaveLength(1)
  })
})

describe('deleting the account', () => {
  async function openDialog(user: UserEvent) {
    await openAccountMenu(user)
    await user.click(screen.getByRole('button', { name: 'Видалити акаунт' }))
    return screen.getByRole('dialog', { name: 'Видалити обліковий запис?' })
  }

  test('warns what is deleted and needs the password', async () => {
    mockApi({ me: signedIn() })
    const user = userEvent.setup()
    render(<App />)

    const dialog = await openDialog(user)

    expect(dialog).toHaveTextContent('обліковий запис alice')
    expect(dialog).toHaveTextContent('усі сесії')
    expect(dialog).toHaveTextContent('уся історія прогнозів')
    const password = within(dialog).getByLabelText('Пароль')
    expect(password).toHaveAttribute('autocomplete', 'current-password')
    expect(password).toHaveFocus()
  })

  test('a wrong password keeps the user signed in and says so', async () => {
    mockApi({
      me: signedIn(),
      deleteAccount: () =>
        jsonResponse({ detail: 'Неправильний пароль.', code: 'wrong_password' }, 403),
    })
    const user = userEvent.setup()
    render(<App />)
    const dialog = await openDialog(user)

    await user.type(within(dialog).getByLabelText('Пароль'), 'wrong-horse-22')
    await user.click(within(dialog).getByRole('button', { name: 'Видалити назавжди' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Неправильний пароль')
    expect(within(dialog).getByLabelText('Пароль')).toHaveValue('')
    expect(within(banner()).getByRole('button', { name: /alice/ })).toBeInTheDocument()
  })

  test('deleting signs the user out and goes to the main page', async () => {
    const fetchMock = mockApi({ me: signedIn(), deleteAccount: noContent })
    const user = userEvent.setup()
    render(<App />)
    const dialog = await openDialog(user)

    await user.type(within(dialog).getByLabelText('Пароль'), PASSWORD)
    await user.click(within(dialog).getByRole('button', { name: 'Видалити назавжди' }))

    expect(await within(banner()).findByRole('link', { name: 'Увійти' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(window.location.hash).toBe('#/')
    expect(screen.getByRole('status', { name: 'Повідомлення' })).toHaveTextContent(
      'Обліковий запис видалено',
    )
    const [[, init]] = callsTo(fetchMock, '/auth/me', 'DELETE')
    expect(bodyOf(init)).toEqual({ password: PASSWORD })
  })

  test('Скасувати closes the dialog without a request', async () => {
    const fetchMock = mockApi({ me: signedIn() })
    const user = userEvent.setup()
    render(<App />)
    const dialog = await openDialog(user)

    await user.click(within(dialog).getByRole('button', { name: 'Скасувати' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(callsTo(fetchMock, '/auth/me', 'DELETE')).toHaveLength(0)
    expect(within(banner()).getByRole('button', { name: /alice/ })).toHaveFocus()
  })
})

// --- Nothing is kept in the browser's storage ------------------------------------------------

test('accounts leave nothing in localStorage or sessionStorage', async () => {
  mockApi({
    register: () => jsonResponse(makeUser(), 201),
    login: signedIn(),
    deleteAccount: noContent,
    predict: () => jsonResponse(makePrediction()),
  })
  window.location.hash = '#/register'
  const user = userEvent.setup()
  render(<App />)

  await user.type(await screen.findByLabelText('Ім’я користувача'), 'alice')
  await user.type(field('Пароль'), PASSWORD)
  await user.type(field('Підтвердження пароля'), PASSWORD)
  await user.click(within(form('Реєстрація')).getByRole('button', { name: 'Зареєструватися' }))
  await within(banner()).findByRole('button', { name: /alice/ })
  await analyze(user)
  await screen.findByTestId('predicted-class')
  await openAccountMenu(user)
  await user.click(screen.getByRole('button', { name: 'Вийти' }))
  await user.click(await within(banner()).findByRole('link', { name: 'Увійти' }))
  await user.type(await screen.findByLabelText('Ім’я користувача'), 'alice')
  await user.type(field('Пароль'), PASSWORD)
  await user.click(within(form('Вхід')).getByRole('button', { name: 'Увійти' }))
  await openAccountMenu(user)
  await user.click(screen.getByRole('button', { name: 'Видалити акаунт' }))
  await user.type(within(screen.getByRole('dialog')).getByLabelText('Пароль'), PASSWORD)
  await user.click(screen.getByRole('button', { name: 'Видалити назавжди' }))
  await waitFor(() => expect(within(banner()).getByRole('link', { name: 'Увійти' })).toBeVisible())

  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

// --- GET /auth/me fails for a while ---------------------------------------------------------

describe('when GET /auth/me fails for a while (429, 503, no connection)', () => {
  const failures = [
    ['429', () => tooMany(30, 'Забагато запитів. Спробуйте ще раз через 30 с.')],
    ['503', () => jsonResponse({ detail: 'База даних недоступна. Спробуйте пізніше.' }, 503)],
    ['no connection', () => Promise.reject(new TypeError('Failed to fetch'))],
  ] as const

  test.each(failures)('%s does not make the user a guest or show anything extra', async (_, me) => {
    const fetchMock = mockApi({ me, ...historyHandlers() })
    window.location.hash = '#/history'

    render(<App />)

    // The cookie may well be valid: the history is asked for as usual.
    expect(await screen.findByRole('heading', { name: 'Історія прогнозів' })).toBeInTheDocument()
    expect(callsTo(fetchMock, '/auth/me')).toHaveLength(1)
    expect(within(banner()).queryByRole('link', { name: 'Увійти' })).not.toBeInTheDocument()
    expect(
      within(banner()).queryByRole('link', { name: 'Зареєструватися' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Історія доступна після входу' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'Повідомлення' })).not.toBeInTheDocument()
  })

  test('a 401 of the history then shows a guest, without "the session ended"', async () => {
    mockApi({
      me: () => tooMany(30, 'Забагато запитів. Спробуйте ще раз через 30 с.'),
      predictions: sessionExpired,
      stats: sessionExpired,
    })
    window.location.hash = '#/history'

    render(<App />)

    expect(
      await screen.findByRole('heading', { name: 'Історія доступна після входу' }),
    ).toBeInTheDocument()
    expect(within(banner()).getByRole('link', { name: 'Увійти' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  test('it is asked again after Retry-After, and the user appears', async () => {
    let calls = 0
    const fetchMock = mockApi({
      me: () =>
        ++calls === 1
          ? tooMany(1, 'Забагато запитів. Спробуйте ще раз через 1 с.')
          : jsonResponse(makeUser()),
    })

    render(<App />)

    expect(
      await within(banner()).findByRole('button', { name: /alice/ }, { timeout: 3000 }),
    ).toBeInTheDocument()
    expect(callsTo(fetchMock, '/auth/me')).toHaveLength(2)
  })
})

// --- Review fixes ------------------------------------------------------------------------------

describe('sign-in errors told apart by their code', () => {
  async function signIn(user: UserEvent) {
    await user.type(await screen.findByLabelText('Ім’я користувача'), 'alice')
    await user.type(field('Пароль'), PASSWORD)
    await user.click(within(form('Вхід')).getByRole('button', { name: 'Увійти' }))
    return within(form('Вхід')).findByRole('alert')
  }

  test('the per-minute limit is not called a lock after failed attempts', async () => {
    mockApi({ login: () => tooMany(42, 'Забагато запитів. Спробуйте ще раз через 42 с.') })
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    const alert = await signIn(user)

    expect(alert).toHaveTextContent('Забагато запитів')
    expect(alert).toHaveTextContent('Спробуйте ще раз через 42 с.')
    expect(alert).not.toHaveTextContent('заблоковано')
  })

  test('a body over 8 KiB is about the name or password, not a file', async () => {
    mockApi({ login: bodyTooLarge })
    window.location.hash = '#/login'
    const user = userEvent.setup()
    render(<App />)

    const alert = await signIn(user)

    expect(alert).toHaveTextContent('Задовгі дані')
    expect(alert).not.toHaveTextContent('Файл')
  })
})

describe('the fields are no longer than the backend allows', () => {
  test('sign-in and registration', async () => {
    mockApi()
    window.location.hash = '#/login'
    render(<App />)

    expect(await screen.findByLabelText('Ім’я користувача')).toHaveAttribute('maxlength', '32')
    expect(field('Пароль')).toHaveAttribute('maxlength', '128')

    window.location.hash = '#/register'
    await screen.findByRole('heading', { name: 'Реєстрація' })
    expect(field('Ім’я користувача')).toHaveAttribute('maxlength', '32')
    expect(field('Пароль')).toHaveAttribute('maxlength', '128')
    expect(field('Підтвердження пароля')).toHaveAttribute('maxlength', '128')
  })

  test('the password of the account deletion', async () => {
    mockApi({ me: signedIn() })
    const user = userEvent.setup()
    render(<App />)
    await openAccountMenu(user)
    await user.click(screen.getByRole('button', { name: 'Видалити акаунт' }))

    expect(within(screen.getByRole('dialog')).getByLabelText('Пароль')).toHaveAttribute(
      'maxlength',
      '128',
    )
  })
})

describe('without a database (DATABASE_URL not set)', () => {
  test('the visitor is a guest at once, and GET /auth/me is not asked again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const fetchMock = mockApi({ me: databaseNotConfigured })

      render(<App />)

      expect(await within(banner()).findByRole('link', { name: 'Увійти' })).toBeInTheDocument()
      await act(() => vi.advanceTimersByTimeAsync(5 * 60_000))
      expect(callsTo(fetchMock, '/auth/me')).toHaveLength(1)
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
